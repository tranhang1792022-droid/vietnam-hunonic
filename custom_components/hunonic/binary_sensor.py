"""Binary Sensor cho thiết bị Hunonic (chuông cửa RF, cảm biến cửa)."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DOORBELL_TYPES
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập binary sensor entities."""

    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        rt = device.get("root_type", "")
        ents = []
        if rt in DOORBELL_TYPES:
            ents.append(HunonicDoorbellBinarySensor(coordinator, device))
        return ents

    setup_entities(hass, entry, async_add_entities, _build)


class HunonicDoorbellBinarySensor(CoordinatorEntity[HunonicCoordinator], BinarySensorEntity):
    """Cảm biến phát hiện chuông cửa RF Hunonic được bấm."""

    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))
        self._last_ring_time: float = 0.0

    @property
    def unique_id(self) -> str:
        return f"hunonic_binary_sensor_{self._device_id}_doorbell"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', 'Chuông cửa')} - Chuông reo"

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, self._root_id)},
            name=str(self._device.get("name", self._device_id)),
            manufacturer="Hunonic",
            model=self._root_type,
        )
        hid = self._device.get("home_id")
        if hid:
            info["via_device"] = (DOMAIN, f"home_{hid}")
        return info

    @property
    def available(self) -> bool:
        return self.coordinator.is_device_online(self._device_id)

    @property
    def is_on(self) -> bool:
        """Trả về True khi có tín hiệu chuông reo trong vòng 5 giây."""
        state = self.coordinator.get_device_state(self._root_id)
        # Kiểm tra action = 1 hoặc ring = 1 từ MQTT realtime
        action = state.get("action")
        if action is not None:
            try:
                if int(action) == 1:
                    now = time.time()
                    if now - self._last_ring_time > 10:
                        self._last_ring_time = now
            except (TypeError, ValueError):
                pass

        if self._last_ring_time > 0 and (time.time() - self._last_ring_time < 5):
            return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "device_type": self._root_type,
            "root_id": self._root_id,
            "last_ring_timestamp": self._last_ring_time if self._last_ring_time > 0 else None,
        }
