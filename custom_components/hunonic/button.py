"""Button entity cho chuông cửa RF Hunonic (rfdb).

Tạo 1 nút bấm "Reo chuông" để kích hoạt chuông từ Home Assistant.
Payload MQTT gửi action=1 (ON pulse) — thiết bị phát âm thanh chuông 1 lần.

Cách dùng trong HA:
  - Bấm nút → chuông kêu (automation, script, dashboard button card).
  - Kết hợp binary_sensor "Chuông bấm" để tạo automation thông báo.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CHIME_TYPES,
    DOMAIN,
    DOORBELL_TYPES,
    IR_FAN_BTN_NATURAL,
    IR_FAN_BTN_SPEED_UP,
    IR_FAN_BTN_SWING,
    IR_FAN_REMOTE_TYPES,
)
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập button entities cho chuông cửa và quạt IR."""

    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        rt = device.get("root_type")
        ents = []
        if rt in DOORBELL_TYPES:
            ents.append(HunonicDoorbellButton(coordinator, device))
        if rt in IR_FAN_REMOTE_TYPES:
            ents.extend([
                HunonicIRFanActionButton(
                    coordinator, device, "Quay (Đảo gió)", "mdi:rotate-3d-variant", IR_FAN_BTN_SWING, "swing"
                ),
                HunonicIRFanActionButton(
                    coordinator, device, "Gió tự nhiên", "mdi:weather-windy", IR_FAN_BTN_NATURAL, "natural"
                ),
                HunonicIRFanActionButton(
                    coordinator, device, "Tăng tốc độ", "mdi:speedometer", IR_FAN_BTN_SPEED_UP, "speed_up"
                ),
            ])
        return ents

    setup_entities(hass, entry, async_add_entities, _build)



class HunonicDoorbellButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút bấm 'Reo chuông' cho thiết bị chuông cửa RF Hunonic.

    Bấm nút → gửi MQTT action=1 → chuông kêu 1 lần.
    ButtonEntity phù hợp hơn SwitchEntity vì đây là trigger (xung), không phải
    trạng thái bật/tắt — HA không lưu state sau khi bấm.
    """

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))

    @property
    def unique_id(self) -> str:
        return f"hunonic_button_{self._device_id}_ring"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', 'Chuong cua')} - Reo chuong"

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

    async def async_press(self) -> None:
        """Bấm nút -> kích hoạt chuông hiện tại và TẤT CẢ thiết bị hsrf cùng kêu lên."""
        # 1. Gửi lệnh tới thiết bị hiện tại
        payload: dict[str, Any] = {
            "u": self._uid,
            self._root_type: 0,
            "act_id": 0,
            "action": 1,
            "src": 1,
        }
        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("Đã gửi lệnh reo chuông tới: %s", self._device.get("name"))

        # 2. Tìm tất cả thiết bị hsrf (Hunonic Smart RF / Chuông cắm điện) cùng tài khoản
        devices = (self.coordinator.data or {}).get("devices", [])
        hsrf_devices = [
            d for d in devices
            if d.get("root_type") in CHIME_TYPES
            and str(d.get("id")) != self._device_id
        ]

        for hsrf_dev in hsrf_devices:
            rt = str(hsrf_dev.get("root_type", "hsrf"))
            hsrf_payload: dict[str, Any] = {
                "u": self._uid,
                rt: 0,
                "act_id": 0,
                "action": 1,
                "src": 1,
            }
            await self.coordinator.async_control_device(hsrf_dev, hsrf_payload)
            _LOGGER.info("Đã gửi lệnh reo chuông tới hsrf: %s (id: %s)", hsrf_dev.get("name"), hsrf_dev.get("id"))

    @property
    def _uid(self) -> int:
        try:
            return int(self.coordinator._user_id or 0)
        except (TypeError, ValueError):
            return 0


class HunonicIRFanActionButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút hành động nhanh cho quạt IR (Quay, Gió tự nhiên, Tăng tốc độ)."""

    def __init__(
        self,
        coordinator: HunonicCoordinator,
        device: dict[str, Any],
        btn_label: str,
        icon: str,
        action: int,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))
        self._btn_label = btn_label
        self._attr_icon = icon
        self._action = action
        self._suffix = suffix

    @property
    def unique_id(self) -> str:
        return f"hunonic_button_{self._device_id}_{self._suffix}"

    @property
    def name(self) -> str:
        dev_name = self._device.get("name", self._device_id)
        return f"{dev_name} - {self._btn_label}"

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

    async def async_press(self) -> None:
        """Bấm nút gửi lệnh IR."""
        payload = {
            "u": int(self.coordinator._user_id or 0),
            self._root_type: 0,
            "act_id": 0,
            "action": self._action,
            "src": 1,
        }
        await self.coordinator.async_control_device(self._device, payload)

