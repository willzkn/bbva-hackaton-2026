"""Estrategia de despacho de LOG-8092.

Despacha cada grupo compatible (destino, zona, intento y requisitos) en cuanto
puede, eligiendo el transportista que minimiza ``coste + LAMBDA * minutos
esperados``. Los minutos esperados incluyen recogida, trayecto y el retraso de
un reintento ponderado por la probabilidad pública de fallo. Si el mejor
transportista quedará libre antes de que venza la retención de 45 minutos y
compensa esperarlo, el grupo espera; al vencer, sale con el mejor disponible.
"""

from __future__ import annotations

import heapq
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from interfaces import (
    BaseDispatcherStrategy,
    CarrierService,
    CarrierSnapshot,
    DeliveryAttemptResult,
    DispatchContext,
    FleetUpgradeDecision,
    MonthlyPlanningContext,
    Shipment,
)


# Céntimos que se está dispuesto a pagar por cada minuto de entrega ahorrado.
LAMBDA_CENTS_PER_MIN = 30
# Minutos que añade un intento fallido antes del siguiente (espera de 24 h).
RETRY_DELAY_MIN = 1440
DEFAULT_DISTANCES = {"URBANA": 12, "SUBURBANA": 35, "RURAL": 80, "REMOTA": 150}


def _round_half_up(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


class _Carrier:
    """Datos de un transportista usados para puntuar, con su flota estimada."""

    __slots__ = (
        "cid", "zones", "caps", "capacity", "pickup", "speed", "fee",
        "surcharge", "night_bps", "holiday_bps", "op_fail", "operates",
        "base", "inflight",
    )

    def __init__(self, snap: CarrierSnapshot) -> None:
        self.cid = snap.carrier_id
        self.inflight: List[int] = []
        self.update(snap, 0)

    def update(self, snap: CarrierSnapshot, now_ts: int) -> None:
        self.zones = frozenset(snap.allowed_zones)
        self.caps = frozenset(snap.capabilities)
        self.capacity = snap.batch_capacity
        self.pickup = snap.pickup_minutes
        self.speed = snap.speed_kmh * snap.speed_multiplier_bps
        self.fee = snap.base_fee_cents
        self.surcharge = {zp.zone: zp.surcharge_cents for zp in snap.zone_prices}
        self.night_bps = snap.night_multiplier_bps
        self.holiday_bps = snap.holiday_multiplier_bps
        self.op_fail = snap.operational_failure_bps
        self.operates = snap.operates_now
        inflight = self.inflight
        while inflight and inflight[0] <= now_ts:
            heapq.heappop(inflight)
        # Unidades utilizables = libres ahora + las que tenemos en viaje.
        self.base = snap.available_units + len(inflight)

    def available(self, now_ts: int) -> int:
        inflight = self.inflight
        while inflight and inflight[0] <= now_ts:
            heapq.heappop(inflight)
        return self.base - len(inflight)


class CandidateStrategy(BaseDispatcherStrategy):
    def __init__(self, carrier_service: CarrierService) -> None:
        super().__init__(carrier_service)
        self.carriers: List[_Carrier] = []
        self.by_id: Dict[str, _Carrier] = {}
        # Pendientes listos agrupados por clave de lote compatible.
        self.groups: Dict[tuple, List[Shipment]] = {}
        # Reintentos a la espera: (ready_ts, shipment_id, shipment).
        self.retries: List[Tuple[int, str, Shipment]] = []
        self.snap_date = None
        self.anchor = 0
        self.configured = False
        self.tick_s = 900
        self.hold_s = 2700
        self.distances = dict(DEFAULT_DISTANCES)
        self.holidays = frozenset()
        self.extra_card = 120
        self.metal_card = 450
        self.penalty = 2000
        self.attempt_bps = (10_000, 5_000, 10_000)
        self.route_bps: Dict[str, int] = {}
        self.absence_bps: Dict[Tuple[int, str], int] = {}
        self.sunday_op = 0
        self.holiday_op = 0

    # ------------------------------------------------------------------ hooks

    def on_monthly_planning(
        self, context: MonthlyPlanningContext
    ) -> Tuple[FleetUpgradeDecision, ...]:
        if not self.configured:
            self._configure(context)
        self._refresh(context.now, context.carriers)
        return ()

    def on_shipments_available(
        self, now: datetime, shipments: Tuple[Shipment, ...]
    ) -> None:
        groups = self.groups
        for shipment in shipments:
            key = (
                shipment.delivery_cluster_id,
                shipment.zone,
                shipment.attempt_number,
                shipment.required_capabilities,
            )
            members = groups.get(key)
            if members is None:
                groups[key] = [shipment]
            else:
                members.append(shipment)
        if self.carriers:
            self._dispatch(now, False)

    def on_dispatch_tick(self, context: DispatchContext) -> None:
        if not self.configured:
            self._configure(context)
        if not self.groups and not self.retries:
            return
        self._refresh(context.now, context.carriers)
        self._dispatch(context.now, True)

    def on_delivery_succeeded(self, result: DeliveryAttemptResult) -> None:
        pass

    def on_delivery_failed(self, result: DeliveryAttemptResult) -> None:
        if result.terminal:
            return
        shipment = result.shipment
        heapq.heappush(
            self.retries,
            (int(shipment.ready_at.timestamp()), shipment.shipment_id, shipment),
        )

    # ------------------------------------------------------------ preparación

    def _configure(self, context) -> None:
        self.configured = True
        self.anchor = int(context.now.timestamp())
        self.holidays = frozenset(context.calendar.holidays)
        policy = context.failure_policy
        self.route_bps = {z.zone: z.inaccessible_route_bps for z in policy.zones}
        modifiers = {z.zone: z.absence_modifier_bps for z in policy.zones}
        for rate in policy.attempts:
            for zone, modifier in modifiers.items():
                self.absence_bps[(rate.attempt_number, zone)] = min(
                    10_000, rate.customer_absence_bps + modifier
                )
        self.sunday_op = policy.sunday_operation_modifier_bps
        self.holiday_op = policy.holiday_operation_modifier_bps
        decision = context.decision_policy
        if decision is not None:
            pricing = decision.pricing
            self.tick_s = pricing.dispatch_tick_minutes * 60
            self.hold_s = pricing.max_voluntary_hold_minutes * 60
            self.extra_card = pricing.additional_card_cents
            self.metal_card = pricing.metal_security_cents
            self.penalty = pricing.terminal_penalty_cents
            self.attempt_bps = tuple(pricing.attempt_multipliers_bps)
            self.distances = {d.zone: d.distance_km for d in decision.distances}

    def _refresh(self, now: datetime, snapshots: Tuple[CarrierSnapshot, ...]) -> None:
        now_ts = int(now.timestamp())
        by_id = self.by_id
        for snap in snapshots:
            carrier = by_id.get(snap.carrier_id)
            if carrier is None:
                carrier = _Carrier(snap)
                by_id[snap.carrier_id] = carrier
                self.carriers.append(carrier)
            carrier.update(snap, now_ts)
        self.snap_date = now.date()

    # ---------------------------------------------------------------- cálculo

    def _travel_min(self, carrier: _Carrier, zone: str) -> int:
        distance = self.distances[zone]
        return -(-distance * 600_000 // carrier.speed)

    def _linehaul(self, carrier: _Carrier, zone: str, attempt: int, when: datetime) -> int:
        hour = when.hour
        night = carrier.night_bps if (hour >= 20 or hour < 6) else 10_000
        calendar = (
            carrier.holiday_bps
            if when.isoweekday() == 7 or when.date() in self.holidays
            else 10_000
        )
        linehaul = carrier.fee + carrier.surcharge[zone]
        return _round_half_up(
            linehaul * self.attempt_bps[attempt - 1] * night * calendar,
            1_000_000_000_000,
        )

    def _expected_min(self, carrier: _Carrier, zone: str, attempt: int, when: datetime) -> float:
        """Minutos esperados hasta entregar con este transportista."""
        trip = carrier.pickup + self._travel_min(carrier, zone)
        route = 0 if "HARD_ACCESS" in carrier.caps else self.route_bps.get(zone, 0)
        operation = carrier.op_fail
        if when.isoweekday() == 7:
            operation += self.sunday_op
        if when.date() in self.holidays:
            operation += self.holiday_op
        absence = self.absence_bps.get((attempt, zone), 0)
        success = (
            (10_000 - min(route, 10_000))
            * (10_000 - min(absence, 10_000))
            * (10_000 - min(operation, 10_000))
        ) / 1e12
        fail = 1.0 - success
        if attempt >= len(self.attempt_bps):
            # Tercer fallo: vuelve a custodia, penaliza y no se entrega.
            return trip + fail * (2 * RETRY_DELAY_MIN + self.penalty / LAMBDA_CENTS_PER_MIN)
        return trip + fail * (RETRY_DELAY_MIN + trip)

    def _operates(self, carrier: _Carrier, when: datetime) -> bool:
        if when.date() == self.snap_date:
            return carrier.operates
        # Sin vista del día: los días laborables no festivos siempre se opera.
        if when.isoweekday() != 7 and when.date() not in self.holidays:
            return True
        return carrier.operates

    def _ceil_tick(self, ts: int) -> int:
        delta = ts - self.anchor
        return self.anchor + -(-delta // self.tick_s) * self.tick_s

    # --------------------------------------------------------------- despacho

    def _dispatch(self, now: datetime, at_tick: bool) -> None:
        now_ts = int(now.timestamp())
        retries = self.retries
        groups = self.groups
        while retries and retries[0][0] <= now_ts:
            _, _, shipment = heapq.heappop(retries)
            key = (
                shipment.delivery_cluster_id,
                shipment.zone,
                shipment.attempt_number,
                shipment.required_capabilities,
            )
            members = groups.get(key)
            if members is None:
                groups[key] = [shipment]
            else:
                members.append(shipment)
        if not groups:
            return

        available = {c.cid: c.available(now_ts) for c in self.carriers}
        operates = {c.cid: self._operates(c, now) for c in self.carriers}
        # Huecos futuros ya prometidos a otros grupos en esta ronda.
        claimed: Dict[str, int] = {}

        order = sorted(
            groups.items(),
            key=lambda item: (
                item[1][0].product != "VIP_BLACK",
                min(s.ready_at for s in item[1]),
            ),
        )
        for key, members in order:
            cluster, zone, attempt, required = key
            ready_ts = int(min(s.ready_at for s in members).timestamp())
            must_by = self._ceil_tick(ready_ts + self.hold_s)
            must_go = at_tick and now_ts >= must_by
            excluded = set()
            while members:
                best = None
                best_score = 0.0
                for carrier in self.carriers:
                    cid = carrier.cid
                    if (
                        cid in excluded
                        or zone not in carrier.zones
                        or not carrier.caps.issuperset(required)
                        or carrier.capacity < 1
                    ):
                        continue
                    size = min(len(members), carrier.capacity)
                    if available[cid] > 0 and operates[cid]:
                        start = now
                        wait = 0
                    elif must_go:
                        continue
                    else:
                        # Espera al retorno de una unidad dentro de la retención.
                        slot = self._next_free(carrier, now_ts, claimed.get(cid, 0))
                        if slot is None:
                            continue
                        slot = self._ceil_tick(slot)
                        if slot > must_by:
                            continue
                        start = now + timedelta(seconds=slot - now_ts)
                        if not self._operates(carrier, start):
                            continue
                        wait = (slot - now_ts) / 60
                    cost = self._linehaul(carrier, zone, attempt, start)
                    score = cost / size + LAMBDA_CENTS_PER_MIN * (
                        wait + self._expected_min(carrier, zone, attempt, start)
                    )
                    if best is None or score < best_score:
                        best = (carrier, size, wait)
                        best_score = score
                if best is None:
                    break
                carrier, size, wait = best
                if wait:
                    claimed[carrier.cid] = claimed.get(carrier.cid, 0) + 1
                    break
                chunk = members[:size]
                receipt = self.carrier_service.dispatch_batch(
                    carrier.cid, tuple(s.shipment_id for s in chunk)
                )
                if receipt.accepted:
                    del members[:size]
                    available[carrier.cid] -= 1
                    eta_ts = int(receipt.estimated_delivery_at.timestamp())
                    heapq.heappush(
                        carrier.inflight,
                        eta_ts + 60 * self._travel_min(carrier, zone),
                    )
                else:
                    reason = receipt.rejection_reason
                    if reason == "NO_AVAILABLE_UNIT":
                        available[carrier.cid] = 0
                    elif reason == "OUTSIDE_OPERATING_WINDOW":
                        operates[carrier.cid] = False
                    else:
                        excluded.add(carrier.cid)
            if not members:
                del groups[key]

    def _next_free(self, carrier: _Carrier, now_ts: int, skip: int) -> Optional[int]:
        """Instante en que queda libre la unidad número ``skip`` en volver."""
        inflight = carrier.inflight
        if skip >= len(inflight):
            return None
        if skip == 0:
            return inflight[0]
        return heapq.nsmallest(skip + 1, inflight)[-1]
