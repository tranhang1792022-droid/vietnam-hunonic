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


def _is_doorbell_or_chime(device: dict[str, Any]) -> bool:
    """Kiểm tra xem thiết bị có phải là chuông cửa (rfdb) hoặc loa chuông (hsrf) không."""
    rt = str(device.get("root_type") or "").lower().strip()
    name = str(device.get("name") or "").lower().strip()
    if any(t in rt for t in ("rfdb", "rfbell", "doorbell", "hsrf", "chime", "bell")):
        return True
    if any(n in name for n in ("chuông", "chuong", "hsrf", "chime", "bell")):
        return True
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập button entities cho chuông cửa và quạt IR."""

    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        rt = device.get("root_type")
        ents = []
        if _is_doorbell_or_chime(device):
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
    """Nút bấm 'Reo chuông' cho thiết bị chuông cửa RF Hunonic (hsrf / rfdb).

    Bấm nút → gửi MQTT action=1 tới tất cả thiết bị hsrf / chuông cắm điện trong nhà.
    Tự động gửi reset sau 2 giây để chuông sẵn sàng cho lần bấm tiếp theo.
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
        """Bấm nút -> kích hoạt TẤT CẢ các thiết bị chuông hsrf / doorbell cùng reo lên."""
        import asyncio
        devices = (self.coordinator.data or {}).get("devices", [])

        # 1. Tìm tất cả thiết bị chuông / hsrf trong tài khoản
        chime_targets: list[dict[str, Any]] = []
        for d in devices:
            if _is_doorbell_or_chime(d):
                chime_targets.append(d)

        # Đảm bảo thiết bị hiện tại luôn có trong danh sách
        target_ids = {str(d.get("id")) for d in chime_targets if d.get("id")}
        if str(self._device_id) not in target_ids:
            chime_targets.append(self._device)

        _LOGGER.info(
            "Hunonic: Kích hoạt reo chuông tới %d thiết bị: %s",
            len(chime_targets),
            [d.get("name") for d in chime_targets],
        )

        # 2. Gửi lệnh BẬT reo chuông tới từng thiết bị
        for dev in chime_targets:
            rt = str(dev.get("root_type") or "hsrf").strip()
            idx = max(1, int(dev.get("index_in_root", 1)))
            ch = max(0, idx - 1)
            act_on = 2 * idx - 1  # 1 cho kênh 1, 3 cho kênh 2

            payload: dict[str, Any] = {
                "u": self._uid,
                rt: ch,
                "hsrf": ch,
                "act_id": 0,
                "action": act_on,
                "src": 1,
                "ring": 1,
                "bell": 1,
            }
            if dev.get("id"):
                try:
                    payload["child_id"] = int(dev.get("id"))
                except (ValueError, TypeError):
                    payload["child_id"] = dev.get("id")

            await self.coordinator.async_control_device(dev, payload)
            _LOGGER.debug("Đã gửi lệnh reo chuông tới: %s (action=%s)", dev.get("name"), act_on)

        # 3. Tự động gửi lệnh OFF sau 2 giây để reset trạng thái chuông sẵn sàng cho lần bấm tiếp theo
        async def _reset_chimes():
            await asyncio.sleep(2.0)
            for dev in chime_targets:
                rt = str(dev.get("root_type") or "hsrf").strip()
                idx = max(1, int(dev.get("index_in_root", 1)))
                ch = max(0, idx - 1)
                act_off = 2 * idx  # 2 cho kênh 1, 4 cho kênh 2
                payload_off: dict[str, Any] = {
                    "u": self._uid,
                    rt: ch,
                    "hsrf": ch,
                    "act_id": 0,
                    "action": act_off,
                    "src": 1,
                }
                if dev.get("id"):
                    try:
                        payload_off["child_id"] = int(dev.get("id"))
                    except (ValueError, TypeError):
                        payload_off["child_id"] = dev.get("id")
                await self.coordinator.async_control_device(dev, payload_off)
            _LOGGER.debug("Đã reset trạng thái các thiết bị chuông về OFF")

        self.hass.async_create_task(_reset_chimes())

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

