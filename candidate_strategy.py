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
from collections import deque
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Tuple

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
LAMBDA_CENTS_PER_MIN = 10
# Minutos que añade un intento fallido antes del siguiente (espera de 24 h).
RETRY_DELAY_MIN = 1440
# Minutos que se retiene una tarjeta cuyo destino ya recibió otra reciente.
MATE_HOLD_MIN = 15
DEFAULT_DISTANCES = {"URBANA": 12, "SUBURBANA": 35, "RURAL": 80, "REMOTA": 150}


def _round_half_up(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


class _Carrier:
    """Datos de un transportista usados para puntuar, con su flota estimada."""

    __slots__ = (
        "cid", "zones", "caps", "capacity", "pickup", "speed", "fee",
        "surcharge", "night_bps", "holiday_bps", "op_fail", "operates",
        "base", "inflight", "supported", "base_speed",
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
        self.base_speed = snap.speed_kmh * 10_000
        self.supported = frozenset(snap.supported_upgrades)
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
        self._options: Dict[tuple, Tuple[int, float]] = {}
        # Destinos con llegadas en la última ventana de retención.
        self.recent: Dict[str, int] = {}
        self.recent_queue: Deque[Tuple[int, str]] = deque()
        # Grupos retenidos a la espera de más tarjetas del mismo destino.
        self.hold_until: Dict[tuple, int] = {}
        # Señales del periodo para decidir upgrades (en unidades de puntuación).
        self.regret: Dict[str, float] = {}
        self.speed_gain: Dict[str, float] = {}
        self.leased: Dict[str, int] = {}
        self.upgrade_rules: Dict[str, Tuple[int, int]] = {
            "EXTRA_UNIT": (40_000, 3),
            "SPEED_SHIFT": (60_000, 1),
        }

    # ------------------------------------------------------------------ hooks

    def on_monthly_planning(
        self, context: MonthlyPlanningContext
    ) -> Tuple[FleetUpgradeDecision, ...]:
        if not self.configured:
            self._configure(context)
        self._refresh(context.now, context.carriers)
        decisions = self._plan_upgrades(context)
        self.regret.clear()
        self.speed_gain.clear()
        return decisions

    def on_shipments_available(
        self, now: datetime, shipments: Tuple[Shipment, ...]
    ) -> None:
        groups = self.groups
        now_ts = int(now.timestamp())
        recent = self.recent
        recent_queue = self.recent_queue
        horizon = now_ts - self.hold_s
        while recent_queue and recent_queue[0][0] < horizon:
            _, old_cluster = recent_queue.popleft()
            if recent.get(old_cluster, 0) <= 1:
                recent.pop(old_cluster, None)
            else:
                recent[old_cluster] -= 1
        for shipment in shipments:
            cluster = shipment.delivery_cluster_id
            key = (
                cluster,
                shipment.zone,
                shipment.attempt_number,
                shipment.required_capabilities,
            )
            members = groups.get(key)
            if members is None:
                groups[key] = [shipment]
            else:
                members.append(shipment)
            if MATE_HOLD_MIN and cluster in recent and key not in self.hold_until:
                # El destino ya recibió tarjetas antes: probablemente lleguen más.
                self.hold_until[key] = now_ts + MATE_HOLD_MIN * 60
        for shipment in shipments:
            cluster = shipment.delivery_cluster_id
            recent[cluster] = recent.get(cluster, 0) + 1
            recent_queue.append((now_ts, cluster))
        if self.carriers:
            self._dispatch(now, False)

    def on_dispatch_tick(self, context: DispatchContext) -> None:
        if not self.configured:
            self._configure(context)
        self._refresh(context.now, context.carriers)
        if self.groups or self.retries:
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
            for upgrade in decision.upgrades:
                self.upgrade_rules[upgrade.upgrade_type] = (
                    upgrade.cost_cents, upgrade.max_per_carrier_period
                )

    def _plan_upgrades(self, context: MonthlyPlanningContext) -> Tuple[FleetUpgradeDecision, ...]:
        """Arrienda unidades o velocidad donde el periodo anterior perdió más."""
        if not context.upgrades_enabled or context.period_index == 0:
            return ()
        decisions = []
        extra_cost, extra_max = self.upgrade_rules.get("EXTRA_UNIT", (0, 0))
        speed_cost, speed_max = self.upgrade_rules.get("SPEED_SHIFT", (0, 0))
        for carrier in self.carriers:
            cid = carrier.cid
            supported = carrier.supported
            if "EXTRA_UNIT" in supported and extra_cost > 0 and extra_max > 0:
                regret = self.regret.get(cid, 0.0)
                quantity = self.leased.get(cid, 0) + int(regret // (2 * extra_cost))
                if regret < extra_cost / 4:
                    quantity -= 1
                quantity = max(0, min(extra_max, quantity))
                self.leased[cid] = quantity
                if quantity:
                    decisions.append(FleetUpgradeDecision(cid, "EXTRA_UNIT", quantity))
            if "SPEED_SHIFT" in supported and speed_cost > 0 and speed_max > 0:
                if self.speed_gain.get(cid, 0.0) * LAMBDA_CENTS_PER_MIN > 1.5 * speed_cost:
                    decisions.append(FleetUpgradeDecision(cid, "SPEED_SHIFT", 1))
        return tuple(decisions)

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
        distance = self.distances.get(zone, DEFAULT_DISTANCES.get(zone, 150))
        if carrier.speed <= 0:
            return 100_000
        return -(-distance * 600_000 // carrier.speed)

    def _flags(self, when: datetime) -> Tuple[bool, bool, bool]:
        """(noche, domingo, festivo) del instante, para tarifas y fallos."""
        hour = when.hour
        return (hour >= 20 or hour < 6, when.isoweekday() == 7, when.date() in self.holidays)

    def _option(
        self, carrier: _Carrier, zone: str, attempt: int, flags: Tuple[bool, bool, bool]
    ) -> Tuple[int, float]:
        """(linehaul ajustado en céntimos, minutos esperados hasta entregar)."""
        key = (carrier.cid, carrier.speed, carrier.fee, zone, attempt, flags)
        cached = self._options.get(key)
        if cached is not None:
            return cached
        night, sunday, holiday = flags
        calendar = carrier.holiday_bps if (sunday or holiday) else 10_000
        linehaul = carrier.fee + carrier.surcharge.get(zone, 0)
        attempt_bps = self.attempt_bps[min(attempt, len(self.attempt_bps)) - 1]
        cost = _round_half_up(
            linehaul * attempt_bps * (carrier.night_bps if night else 10_000) * calendar,
            1_000_000_000_000,
        )

        trip = carrier.pickup + self._travel_min(carrier, zone)
        route = 0 if "HARD_ACCESS" in carrier.caps else self.route_bps.get(zone, 0)
        operation = carrier.op_fail
        if sunday:
            operation += self.sunday_op
        if holiday:
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
            expected = trip + fail * (2 * RETRY_DELAY_MIN + self.penalty / LAMBDA_CENTS_PER_MIN)
        else:
            expected = trip + fail * (RETRY_DELAY_MIN + trip)
        result = (cost, expected)
        self._options[key] = result
        return result

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
        free_units = sum(
            count for cid, count in available.items() if count > 0 and operates[cid]
        )
        if free_units <= 0:
            # Sin unidades libres no hay nada que despachar ni infracción posible.
            return
        # Huecos futuros ya prometidos a otros grupos en esta ronda.
        claimed: Dict[str, int] = {}
        flags_now = self._flags(now)

        # Orden de entrada en READY (inserción), con los VIP primero.
        order = [item for item in groups.items() if item[1][0].product == "VIP_BLACK"]
        order.extend(item for item in groups.items() if item[1][0].product != "VIP_BLACK")
        for key, members in order:
            if free_units <= 0:
                break
            cluster, zone, attempt, required = key
            ready_ts = int(members[0].ready_at.timestamp())
            must_by = self._ceil_tick(ready_ts + self.hold_s)
            must_go = at_tick and now_ts >= must_by
            if not must_go and key in self.hold_until:
                if now_ts < self.hold_until[key] and now_ts < must_by:
                    continue
            excluded = set()
            while members:
                best = None
                best_score = 0.0
                # Mejor opción si hubiera flota libre: mide la pérdida por congestión.
                ideal = None
                ideal_score = 0.0
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
                    if operates[cid]:
                        cost, expected = self._option(carrier, zone, attempt, flags_now)
                        score = cost / size + LAMBDA_CENTS_PER_MIN * expected
                        if ideal is None or score < ideal_score:
                            ideal = cid
                            ideal_score = score
                    if available[cid] > 0 and operates[cid]:
                        flags = flags_now
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
                        flags = self._flags(start)
                        wait = (slot - now_ts) / 60
                    cost, expected = self._option(carrier, zone, attempt, flags)
                    score = cost / size + LAMBDA_CENTS_PER_MIN * (wait + expected)
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
                    free_units -= 1
                    eta_ts = int(receipt.estimated_delivery_at.timestamp())
                    heapq.heappush(
                        carrier.inflight,
                        eta_ts + 60 * self._travel_min(carrier, zone),
                    )
                    waited = now_ts - max(ready_ts, self.hold_until.get(key, 0))
                    loss = best_score - ideal_score + LAMBDA_CENTS_PER_MIN * max(waited, 0) / 60
                    if ideal is not None and loss > 0:
                        self.regret[ideal] = self.regret.get(ideal, 0.0) + size * loss
                    if "SPEED_SHIFT" in carrier.supported:
                        distance = self.distances.get(zone, DEFAULT_DISTANCES.get(zone, 150))
                        base = carrier.base_speed
                        gain = -(-distance * 600_000 // base) - -(-distance * 600_000 * 4 // (base * 5))
                        self.speed_gain[carrier.cid] = (
                            self.speed_gain.get(carrier.cid, 0.0) + size * gain
                        )
                else:
                    reason = receipt.rejection_reason
                    if reason in ("NO_AVAILABLE_UNIT", "OUTSIDE_OPERATING_WINDOW"):
                        free_units -= max(available[carrier.cid], 0)
                        available[carrier.cid] = 0
                    else:
                        excluded.add(carrier.cid)
                    if free_units <= 0:
                        break
            if not members:
                del groups[key]
                self.hold_until.pop(key, None)

    def _next_free(self, carrier: _Carrier, now_ts: int, skip: int) -> Optional[int]:
        """Instante en que queda libre la unidad número ``skip`` en volver."""
        inflight = carrier.inflight
        if skip >= len(inflight):
            return None
        if skip == 0:
            return inflight[0]
        return heapq.nsmallest(skip + 1, inflight)[-1]
