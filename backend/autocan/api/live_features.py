"""Feed the offline feature pipeline from live observer deliveries."""

from collections.abc import Callable

from autocan.bus import VirtualCanAdapter
from autocan.clock import SimulationClock
from autocan.events import CanObservation, Delivery
from autocan.features import FeatureConfig, FeaturePipeline, FeatureWindow, replay_observations
from autocan.network_profile import RECEIVERS


class LiveFeatureBus(VirtualCanAdapter):
    def __init__(
        self, end_ns: int, config: FeatureConfig, on_window: Callable[[FeatureWindow], None]
    ) -> None:
        self._clock = SimulationClock()
        self._pipeline = FeaturePipeline(
            self._clock,
            profile_id="democar-network",
            receiver_id="observer",
            end_ns=end_ns,
            config=config,
            on_window=on_window,
        )
        self._receipts: list[CanObservation] = []
        super().__init__(receiver_ids=RECEIVERS)

    def receive(self, receiver_id: str, *, received_at_ns: int) -> Delivery | None:
        delivery = super().receive(receiver_id, received_at_ns=received_at_ns)
        if delivery is not None and receiver_id == "observer":
            self._receipts.append(delivery.observation)
        return delivery

    def advance_features_to(self, sim_time_ns: int) -> None:
        # The simulation has finished all deliveries through this time. Replay
        # them before closing same-time windows, even for a tick with no receipts.
        replay_observations(iter(self._receipts), self._clock, self._pipeline, end_ns=sim_time_ns)
        self._receipts.clear()
