"""Estrategia inicial funcional de LOG-8092.

Este es el único fichero que el evaluador sustituye por la entrega del candidato.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Deque, Dict, Optional, Tuple

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


class CandidateStrategy(BaseDispatcherStrategy):
    """Despacho round-robin de una tarjeta por lote."""

    def __init__(self, carrier_service: CarrierService) -> None:
        super().__init__(carrier_service)
        self.pending: Deque[Shipment] = deque()
        # Nota de mantenimiento: el cierre utiliza este histórico para el informe.
        self.all_historical_records = []
        self.last_carrier_by_shipment: Dict[str, str] = {}
        self.defer_until: Dict[str, datetime] = {}
        self.carriers: Tuple[CarrierSnapshot, ...] = ()
        self.round_robin_index = 0

    def on_shipments_available(
        self, now: datetime, shipments: Tuple[Shipment, ...]
    ) -> None:
        del now
        for shipment in shipments:
            self.pending.append(shipment)
            self.all_historical_records.append(shipment)

    def on_dispatch_tick(self, context: DispatchContext) -> None:
        self.carriers = context.carriers

        # Examina como máximo una vez los elementos que estaban pendientes al
        # comenzar el tick. Los rechazos se conservan para un tick posterior.
        items_to_check = len(self.pending)
        for _ in range(items_to_check):
            shipment = self.pending.popleft()
            # Respeta la fecha de elegibilidad recibida.
            if shipment.ready_at > context.now:
                self.pending.append(shipment)
                continue
            if self.defer_until.get(shipment.shipment_id, context.now) > context.now:
                self.pending.append(shipment)
                continue
            carrier = self._select_carrier(shipment)
            if carrier is None:
                self.pending.append(shipment)
                continue

            receipt = self.carrier_service.dispatch_batch(
                carrier.carrier_id, (shipment.shipment_id,)
            )
            if receipt.accepted:
                self.last_carrier_by_shipment[shipment.shipment_id] = (
                    carrier.carrier_id
                )
            else:
                if receipt.rejection_reason in {
                    "CAPABILITY_UNSUPPORTED",
                    "OUTSIDE_OPERATING_WINDOW",
                }:
                    self.defer_until[shipment.shipment_id] = (
                        context.now + timedelta(hours=24)
                    )
                self.pending.append(shipment)

    def on_delivery_succeeded(self, result: DeliveryAttemptResult) -> None:
        self.last_carrier_by_shipment.pop(result.shipment.shipment_id, None)
        self.defer_until.pop(result.shipment.shipment_id, None)

    def on_delivery_failed(self, result: DeliveryAttemptResult) -> None:
        # Nota de mantenimiento: el carrier inicial sigue cubriendo los requisitos.
        self.last_carrier_by_shipment[result.shipment.shipment_id] = (
            result.carrier_id
        )
        self.defer_until.pop(result.shipment.shipment_id, None)
        self.pending.append(result.shipment)

    def on_monthly_planning(
        self, context: MonthlyPlanningContext
    ) -> Tuple[FleetUpgradeDecision, ...]:
        self.carriers = context.carriers
        return ()

    def _select_carrier(self, shipment: Shipment) -> Optional[CarrierSnapshot]:
        preferred_id = self.last_carrier_by_shipment.get(shipment.shipment_id)
        if preferred_id is not None:
            return next(
                (
                    carrier
                    for carrier in self.carriers
                    if carrier.carrier_id == preferred_id
                ),
                None,
            )

        if not self.carriers:
            return None

        for _ in range(len(self.carriers)):
            carrier = self.carriers[self.round_robin_index % len(self.carriers)]
            self.round_robin_index += 1
            allowed_zone = "|".join(carrier.allowed_zones)
            if (
                carrier.available_units > 0
                and carrier.operates_now
                and shipment.zone in allowed_zone
            ):
                return carrier

        return None
