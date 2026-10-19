"""Pruebas de contrato de la estrategia propia con un servicio simulado."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from typing import List, Sequence, Tuple

from candidate_strategy import CandidateStrategy
from interfaces import (
    DeliveryAttemptResult,
    DispatchContext,
    DispatchReceipt,
    MonthlyPlanningContext,
    Shipment,
    ZonePrice,
)
from test_candidate_strategy_smoke import (
    LIMA,
    carrier_snapshot,
    failure_policy,
    rcp_policy,
    scenario_calendar,
)


def make_carrier(now, carrier_id, units=1, zones=("URBANA",), caps=(), fee=500, speed=40, pickup=10):
    return replace(
        carrier_snapshot(now),
        carrier_id=carrier_id,
        allowed_zones=zones,
        capabilities=caps,
        available_units=units,
        base_total_units=units,
        total_units=units,
        base_fee_cents=fee,
        speed_kmh=speed,
        pickup_minutes=pickup,
        zone_prices=tuple(ZonePrice(zone, 0) for zone in zones),
    )


def make_shipment(sid, now, cluster=None, zone="URBANA", product="STANDARD", attempt=1, caps=()):
    return Shipment(
        shipment_id=sid,
        available_at=now,
        ready_at=now,
        sla_deadline=now + timedelta(days=3),
        product=product,
        zone=zone,
        delivery_cluster_id=cluster or f"C-{sid}",
        is_metal=False,
        state="READY",
        attempt_number=attempt,
        required_capabilities=caps,
    )


class FakeService:
    """Acepta mientras el transportista tenga unidades libres y cubra la zona."""

    def __init__(self, now, carriers) -> None:
        self.now = now
        self.carriers = {c.carrier_id: c for c in carriers}
        self.free = {c.carrier_id: c.available_units for c in carriers}
        self.dispatched: List[Tuple[str, Tuple[str, ...]]] = []
        self.rejected: List[str] = []

    def snapshot(self):
        return tuple(
            replace(c, available_units=self.free[cid]) for cid, c in sorted(self.carriers.items())
        )

    def dispatch_batch(self, carrier_id: str, shipment_ids: Sequence[str]) -> DispatchReceipt:
        ids = tuple(sorted(shipment_ids))
        if self.free.get(carrier_id, 0) <= 0:
            self.rejected.append("NO_AVAILABLE_UNIT")
            return DispatchReceipt(False, carrier_id, ids, self.now, 0, rejection_reason="NO_AVAILABLE_UNIT")
        self.free[carrier_id] -= 1
        self.dispatched.append((carrier_id, ids))
        return DispatchReceipt(
            True, carrier_id, ids, self.now, 500, "B-1", self.now + timedelta(minutes=30)
        )

    def context(self) -> DispatchContext:
        return DispatchContext(
            now=self.now,
            reference_budget_cents=100_000,
            spent_cents=0,
            contingency_threshold_cents=110_000,
            carriers=self.snapshot(),
            calendar=scenario_calendar(),
            failure_policy=failure_policy(),
            rcp_policy=rcp_policy(),
        )

    def planning(self, period: int, enabled: bool = True) -> MonthlyPlanningContext:
        return MonthlyPlanningContext(
            now=self.now,
            period_index=period,
            reference_budget_cents=100_000,
            spent_cents=0,
            contingency_threshold_cents=110_000,
            carriers=self.snapshot(),
            calendar=scenario_calendar(),
            failure_policy=failure_policy(),
            rcp_policy=rcp_policy(),
            upgrades_enabled=enabled,
        )


START = datetime(2026, 1, 5, 0, 0, tzinfo=LIMA)


def started(carriers):
    """Estrategia tras la planificación inicial del día 0, como hace el motor."""
    service = FakeService(START, carriers)
    strategy = CandidateStrategy(service)
    assert strategy.on_monthly_planning(service.planning(0)) == ()
    return service, strategy


class MiEstrategiaTest(unittest.TestCase):
    def test_dispatches_on_arrival_without_waiting_for_tick(self) -> None:
        service, strategy = started([make_carrier(START, "A")])
        now = START + timedelta(hours=9, minutes=7)
        service.now = now
        strategy.on_shipments_available(now, (make_shipment("S1", now),))
        self.assertEqual(service.dispatched, [("A", ("S1",))])

    def test_same_cluster_arrivals_share_one_batch(self) -> None:
        service, strategy = started([make_carrier(START, "A")])
        now = START + timedelta(hours=9)
        service.now = now
        strategy.on_shipments_available(
            now, (make_shipment("S1", now, "K"), make_shipment("S2", now, "K"))
        )
        self.assertEqual(service.dispatched, [("A", ("S1", "S2"))])

    def test_hard_access_requirement_only_uses_capable_carrier(self) -> None:
        carriers = [
            make_carrier(START, "CHEAP", zones=("RURAL",), fee=100),
            make_carrier(START, "HARD", zones=("RURAL",), caps=("HARD_ACCESS",), fee=900),
        ]
        service, strategy = started(carriers)
        now = START + timedelta(hours=9)
        service.now = now
        shipment = make_shipment("S1", now, zone="RURAL", attempt=2, caps=("HARD_ACCESS",))
        strategy.on_shipments_available(now, (shipment,))
        self.assertEqual(service.dispatched, [("HARD", ("S1",))])

    def test_mate_hold_is_released_before_hold_audit(self) -> None:
        service, strategy = started([make_carrier(START, "A", units=5)])
        first = START + timedelta(hours=9)
        service.now = first
        strategy.on_shipments_available(first, (make_shipment("S1", first, "K"),))
        second = first + timedelta(minutes=5)
        service.now = second
        strategy.on_shipments_available(second, (make_shipment("S2", second, "K"),))
        # La segunda tarjeta del destino se retiene a la espera de más.
        self.assertEqual(len(service.dispatched), 1)
        tick = START + timedelta(hours=9, minutes=45)
        service.now = tick
        strategy.on_dispatch_tick(service.context())
        self.assertEqual(service.dispatched[-1], ("A", ("S2",)))
        self.assertFalse(strategy.groups)

    def test_rejection_keeps_shipment_until_unit_frees(self) -> None:
        service, strategy = started([make_carrier(START, "A", units=1)])
        now = START + timedelta(hours=9)
        service.now = now
        service.free["A"] = 0  # el modelo local aún cree que hay unidad libre
        strategy.on_shipments_available(now, (make_shipment("S1", now),))
        self.assertEqual(service.rejected, ["NO_AVAILABLE_UNIT"])
        self.assertTrue(strategy.groups)
        service.free["A"] = 1
        service.now = now + timedelta(minutes=15)
        strategy.on_dispatch_tick(service.context())
        self.assertEqual(service.dispatched, [("A", ("S1",))])

    def test_retry_waits_until_ready_at(self) -> None:
        service, strategy = started([make_carrier(START, "A", units=3)])
        now = START + timedelta(hours=9)
        service.now = now
        shipment = make_shipment("S1", now)
        strategy.on_shipments_available(now, (shipment,))
        failed_at = now + timedelta(minutes=30)
        retry_at = failed_at + timedelta(hours=24)
        retry_view = replace(
            shipment, state="WAITING_RETRY", attempt_number=2,
            ready_at=retry_at, retry_eligible_at=retry_at,
        )
        strategy.on_delivery_failed(DeliveryAttemptResult(
            retry_view, "A", "B-1", 1, failed_at, False, "CUSTOMER_ABSENT"
        ))
        service.now = retry_at - timedelta(minutes=15)
        strategy.on_dispatch_tick(service.context())
        self.assertEqual(len(service.dispatched), 1)
        service.now = retry_at
        strategy.on_dispatch_tick(service.context())
        self.assertEqual(len(service.dispatched), 2)

    def test_terminal_failure_is_not_retried(self) -> None:
        service, strategy = started([make_carrier(START, "A")])
        shipment = replace(make_shipment("S1", START), state="RETURNED_TO_VAULT", attempt_number=3)
        strategy.on_delivery_failed(DeliveryAttemptResult(
            shipment, "A", "B-1", 3, START, True, "CUSTOMER_ABSENT"
        ))
        self.assertFalse(strategy.retries)

    def test_planning_returns_tuple_when_upgrades_disabled(self) -> None:
        service, strategy = started([make_carrier(START, "A")])
        strategy.regret["A"] = 1e9
        self.assertEqual(strategy.on_monthly_planning(service.planning(1, enabled=False)), ())

    def test_congestion_leases_extra_units(self) -> None:
        service, strategy = started([make_carrier(START, "A")])
        strategy.regret["A"] = 1e9
        decisions = strategy.on_monthly_planning(service.planning(1))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].upgrade_type, "EXTRA_UNIT")
        self.assertEqual(decisions[0].quantity, 3)


if __name__ == "__main__":
    unittest.main()
