"""Quạt thông minh Hunonic cho Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FAN_TYPES,
    IR_FAN_BTN_NATURAL,
    IR_FAN_BTN_OFF,
    IR_FAN_BTN_ON,
    IR_FAN_BTN_SPD1,
    IR_FAN_BTN_SPD2,
    IR_FAN_BTN_SPD3,
    IR_FAN_BTN_SPD4,
    IR_FAN_BTN_SPD5,
    IR_FAN_BTN_SPD6,
    IR_FAN_BTN_SPD7,
    IR_FAN_BTN_SPD8,
    IR_FAN_BTN_SPEED_UP,
    IR_FAN_BTN_SWING,
    IR_FAN_BTN_TIMER1,
    IR_FAN_REMOTE_TYPES,
)
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)

# ── Action codes Hunonic quạt ─────────────────────────────────────────────────
# action = 1 → bật tốc độ thấp
# action = 2 → tắt
# action = 3 → tốc độ trung bình
# action = 5 → tốc độ cao
FAN_ACTION_OFF = 2
FAN_ACTION_LOW = 1
FAN_ACTION_MED = 3
FAN_ACTION_HIGH = 5

# Preset modes
PRESET_LOW = "low"
PRESET_MED = "medium"
PRESET_HIGH = "high"

_PRESET_TO_ACTION: dict[str, int] = {
    PRESET_LOW: FAN_ACTION_LOW,
    PRESET_MED: FAN_ACTION_MED,
    PRESET_HIGH: FAN_ACTION_HIGH,
}

_ACTION_TO_PRESET: dict[int, str] = {
    FAN_ACTION_LOW: PRESET_LOW,
    FAN_ACTION_MED: PRESET_MED,
    FAN_ACTION_HIGH: PRESET_HIGH,
}

# Phần trăm tương ứng với preset
_PRESET_TO_PCT: dict[str, int] = {
    PRESET_LOW: 33,
    PRESET_MED: 66,
    PRESET_HIGH: 100,
}


def _pct_to_preset(pct: int) -> str:
    """Chuyển phần trăm (1-100) sang preset name."""
    if pct <= 33:
        return PRESET_LOW
    if pct <= 66:
        return PRESET_MED
    return PRESET_HIGH


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập fan entities (tự thêm thiết bị mới khi danh sách thay đổi)."""
    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        rt = device.get("root_type")
        if rt in FAN_TYPES:
            return [HunonicFan(coordinator, device)]
        # irchildv2 / irremote: tạo cả FAN entity (quạt học lệnh IR).
        # Climate entity (điều hòa) được tạo riêng ở climate.py — user tắt loại
        # không dùng trong HA Settings → Entities.
        if rt in IR_FAN_REMOTE_TYPES:
            name = str(device.get("name", "")).upper()
            if "QUẠT" in name or "FAN" in name:
                return [HunonicIRFan(coordinator, device)]
        return []

    setup_entities(hass, entry, async_add_entities, _build)


class HunonicFan(CoordinatorEntity[HunonicCoordinator], FanEntity):
    """Quạt Hunonic hỗ trợ bật/tắt, 3 mức tốc độ và percentage."""

    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [PRESET_LOW, PRESET_MED, PRESET_HIGH]

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))
        self._last_preset: str = PRESET_MED  # nhớ tốc độ cuối khi tắt/bật lại

    @property
    def unique_id(self) -> str:
        return f"hunonic_fan_{self._device_id}"

    @property
    def name(self) -> str:
        return str(self._device.get("name", f"Fan {self._device_id}"))

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, self._root_id)},
            name=str(self._device.get("name", self._device_id)),
            manufacturer="Hunonic",
            model=self._root_type,
        )
        hid = self._device.get("home_id")
        if hid:  # gom thiết bị dưới "trạm trung chuyển" của nhà
            info["via_device"] = (DOMAIN, f"home_{hid}")
        return info

    @property
    def available(self) -> bool:
        return self.coordinator.is_device_online(self._device_id)

    def _current_action(self) -> int | None:
        """Đọc action code hiện tại từ MQTT state hoặc REST API."""
        state = self.coordinator.get_device_state(self._root_id)
        for key in ("action", self._root_type, "value"):
            val = state.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass

        raw = self.coordinator.get_device_raw(self._device_id)
        for key in ("action", "value"):
            val = raw.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
        return None

    @property
    def is_on(self) -> bool:
        action = self._current_action()
        if action is None:
            return False
        return action != FAN_ACTION_OFF and action != 0

    @property
    def preset_mode(self) -> str | None:
        """Trả về preset hiện tại (low/medium/high) hoặc None nếu tắt."""
        if not self.is_on:
            return None
        action = self._current_action()
        if action is not None:
            return _ACTION_TO_PRESET.get(action)
        return None

    @property
    def percentage(self) -> int | None:
        """Trả về tốc độ hiện tại (0-100%)."""
        if not self.is_on:
            return 0
        preset = self.preset_mode
        if preset:
            return _PRESET_TO_PCT[preset]
        return None

    @property
    def speed_count(self) -> int:
        return 3

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Bật quạt, tuỳ chọn đặt tốc độ."""
        if preset_mode and preset_mode in _PRESET_TO_ACTION:
            target = preset_mode
        elif percentage is not None and percentage > 0:
            target = _pct_to_preset(percentage)
        else:
            target = self._last_preset

        self._last_preset = target
        await self._send_action(_PRESET_TO_ACTION[target])

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Tắt quạt."""
        # Lưu tốc độ hiện tại trước khi tắt
        if self.preset_mode:
            self._last_preset = self.preset_mode
        await self._send_action(FAN_ACTION_OFF)

    async def async_set_percentage(self, percentage: int) -> None:
        """Đặt tốc độ (%)."""
        if percentage == 0:
            await self.async_turn_off()
            return
        preset = _pct_to_preset(percentage)
        self._last_preset = preset
        await self._send_action(_PRESET_TO_ACTION[preset])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Đặt tốc độ theo preset."""
        action = _PRESET_TO_ACTION.get(preset_mode, FAN_ACTION_MED)
        self._last_preset = preset_mode
        await self._send_action(action)

    async def _send_action(self, action: int) -> None:
        """Gửi lệnh action đến quạt."""
        payload = {
            self._root_type: 1 if action != FAN_ACTION_OFF else 0,
            "u": self.coordinator._user_id,
            "act_id": 0,
            "action": action,
            "src": 1,
        }
        await self.coordinator.async_control_device(self._device, payload)


# ── Ánh xạ tốc độ 1..8 sang action code quạt học lệnh IR ─────────────────────
_IR_SPEED_TO_ACTION: dict[int, int] = {
    1: IR_FAN_BTN_SPD1,
    2: IR_FAN_BTN_SPD2,
    3: IR_FAN_BTN_SPD3,
    4: IR_FAN_BTN_SPD4,
    5: IR_FAN_BTN_SPD5,
    6: IR_FAN_BTN_SPD6,
    7: IR_FAN_BTN_SPD7,
    8: IR_FAN_BTN_SPD8,
}

PRESET_NORMAL = "Normal"
PRESET_NATURAL = "Natural"


class HunonicIRFan(CoordinatorEntity[HunonicCoordinator], FanEntity, RestoreEntity):
    """Quạt học lệnh IR Hunonic (irchildv2 / irremote).

    Hỗ trợ đầy đủ:
    - Bật / Tắt (nút xanh ON action=1, nút đỏ OFF action=3)
    - 8 mức tốc độ tương ứng các nút 1..8 (action 7..14)
    - Quay / Đảo gió (Oscillate / Swing, action=5)
    - Chế độ gió tự nhiên (Natural wind, action=6)
    - Nhớ trạng thái qua RestoreEntity
    """

    _attr_speed_count = 8
    _attr_preset_modes = [PRESET_NORMAL, PRESET_NATURAL]
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.OSCILLATE
        | FanEntityFeature.PRESET_MODE
    )
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))

        # Trạng thái quạt
        self._is_on: bool = False
        self._speed: int = 1               # 1..8
        self._oscillating: bool = False     # Quay / đảo gió
        self._preset_mode: str = PRESET_NORMAL

    async def async_added_to_hass(self) -> None:
        """Khôi phục trạng thái lần trước từ HA."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return

        # Khôi phục bật/tắt
        self._is_on = (last.state == "on")

        attrs = last.attributes
        # Khôi phục phần trăm / tốc độ
        pct = attrs.get("percentage")
        if pct is not None:
            try:
                self._speed = max(1, min(8, int(round(float(pct) * 8 / 100))))
            except (TypeError, ValueError):
                pass

        # Khôi phục quay
        osc = attrs.get("oscillating")
        if isinstance(osc, bool):
            self._oscillating = osc

        # Khôi phục preset mode
        preset = attrs.get("preset_mode")
        if preset in self._attr_preset_modes:
            self._preset_mode = preset

    @property
    def unique_id(self) -> str:
        return f"hunonic_ir_fan_{self._device_id}"

    @property
    def name(self) -> str:
        return str(self._device.get("name", f"Quạt IR {self._device_id}"))

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
        return self._is_on

    @property
    def percentage(self) -> int | None:
        """Tốc độ dạng phần trăm (0..100%)."""
        if not self._is_on:
            return 0
        return int(round(self._speed * 100 / 8))

    @property
    def oscillating(self) -> bool | None:
        """Trạng thái quay / đảo gió."""
        return self._oscillating

    @property
    def preset_mode(self) -> str | None:
        """Chế độ gió (Normal / Natural)."""
        return self._preset_mode if self._is_on else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Thuộc tính bổ sung."""
        return {
            "speed_level": self._speed,
            "device_type": self._root_type,
            "root_id": self._root_id,
        }

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Bật quạt IR."""
        if percentage is not None and percentage > 0:
            speed = max(1, min(8, int(round(percentage * 8 / 100))))
            self._speed = speed
            action = _IR_SPEED_TO_ACTION.get(speed, IR_FAN_BTN_ON)
        elif preset_mode == PRESET_NATURAL:
            self._preset_mode = PRESET_NATURAL
            action = IR_FAN_BTN_NATURAL
        else:
            action = _IR_SPEED_TO_ACTION.get(self._speed, IR_FAN_BTN_ON)

        self._is_on = True
        await self._send_cmd(action)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Tắt quạt IR (gửi nút đỏ action=3)."""
        self._is_on = False
        await self._send_cmd(IR_FAN_BTN_OFF)
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Đặt tốc độ quạt (0-100% -> 8 mức)."""
        if percentage == 0:
            await self.async_turn_off()
            return

        speed = max(1, min(8, int(round(percentage * 8 / 100))))
        self._speed = speed
        self._is_on = True
        action = _IR_SPEED_TO_ACTION.get(speed, IR_FAN_BTN_SPD1)
        await self._send_cmd(action)
        self.async_write_ha_state()

    async def async_oscillate(self, oscillating: bool) -> None:
        """Bật/tắt quay (gửi nút quay action=5)."""
        self._oscillating = oscillating
        await self._send_cmd(IR_FAN_BTN_SWING)
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Đặt chế độ gió (Normal / Natural)."""
        self._preset_mode = preset_mode
        if preset_mode == PRESET_NATURAL:
            await self._send_cmd(IR_FAN_BTN_NATURAL)
        else:
            # Quay lại tốc độ bình thường
            action = _IR_SPEED_TO_ACTION.get(self._speed, IR_FAN_BTN_SPD1)
            await self._send_cmd(action)
        self.async_write_ha_state()

    async def _send_cmd(self, action: int) -> None:
        """Gửi payload điều khiển IR tới thiết bị qua MQTT."""
        payload: dict[str, Any] = {
            "u": int(self.coordinator._user_id or 0),
            self._root_type: 0,
            "act_id": 0,
            "action": action,
            "src": 1,
        }
        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id
        await self.coordinator.async_control_device(self._device, payload)

