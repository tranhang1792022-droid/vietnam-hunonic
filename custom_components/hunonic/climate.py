"""Climate entity cho thiết bị IR điều hòa Hunonic (Home Assistant).

Hỗ trợ:
- Bật/tắt qua HVACMode.OFF
- Chế độ: Auto / Cool / Heat / Dry / Fan Only
- Nhiệt độ đặt: 16°C – 30°C (bước 1°C)
- Tốc độ quạt: auto / min / low / medium / high / max

Payload MQTT (đã xác minh cấu trúc từ reverse engineering app Hunonic):
  Bật / đổi chế độ: {"u":<uid>, "<root_type>":0, "act_id":0, "action":1,
                      "mode":<mode>, "temp":<temp>, "fan":<fan>, "src":1}
  Tắt:             {"u":<uid>, "<root_type>":0, "act_id":0, "action":2, "src":1}

State đọc từ MQTT coordinator.get_device_state(root_id) — field "action", "mode",
"temp", "fan". Fallback REST coordinator.get_device_raw() field "value" (JSON str).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    IR_AC_TYPES,
    IR_FAN_AUTO,
    IR_FAN_HIGH,
    IR_FAN_LOW,
    IR_FAN_MAX,
    IR_FAN_MEDIUM,
    IR_FAN_MIN,
    IR_MODE_AUTO,
    IR_MODE_COOL,
    IR_MODE_DRY,
    IR_MODE_FAN,
    IR_MODE_HEAT,
    IR_TEMP_DEFAULT,
    IR_TEMP_MAX,
    IR_TEMP_MIN,
)
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)

# ── Ánh xạ HA HVACMode ↔ Hunonic mode code ───────────────────────────────────

_HVAC_TO_MODE: dict[HVACMode, int] = {
    HVACMode.AUTO:     IR_MODE_AUTO,
    HVACMode.COOL:     IR_MODE_COOL,
    HVACMode.DRY:      IR_MODE_DRY,
    HVACMode.FAN_ONLY: IR_MODE_FAN,
    HVACMode.HEAT:     IR_MODE_HEAT,
}

_MODE_TO_HVAC: dict[int, HVACMode] = {v: k for k, v in _HVAC_TO_MODE.items()}

# ── Ánh xạ fan speed label ↔ Hunonic fan code ────────────────────────────────

FAN_AUTO   = "auto"
FAN_MIN    = "min"
FAN_LOW    = "low"
FAN_MEDIUM = "medium"
FAN_HIGH   = "high"
FAN_MAX    = "max"

_FAN_LABEL_TO_CODE: dict[str, int] = {
    FAN_AUTO:   IR_FAN_AUTO,
    FAN_MIN:    IR_FAN_MIN,
    FAN_LOW:    IR_FAN_LOW,
    FAN_MEDIUM: IR_FAN_MEDIUM,
    FAN_HIGH:   IR_FAN_HIGH,
    FAN_MAX:    IR_FAN_MAX,
}

_FAN_CODE_TO_LABEL: dict[int, str] = {v: k for k, v in _FAN_LABEL_TO_CODE.items()}

_ALL_FAN_MODES = [FAN_AUTO, FAN_MIN, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_MAX]
_ALL_HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.AUTO,
    HVACMode.COOL,
    HVACMode.HEAT,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]

# ── Setup entry ───────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập climate entities IR (tự thêm thiết bị mới khi danh sách thay đổi)."""

    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        if device.get("root_type") in IR_AC_TYPES:
            return [HunonicIRClimate(coordinator, device)]
        return []

    setup_entities(hass, entry, async_add_entities, _build)


# ── Entity ────────────────────────────────────────────────────────────────────

class HunonicIRClimate(CoordinatorEntity[HunonicCoordinator], ClimateEntity, RestoreEntity):
    """Điều hòa IR Hunonic cho Home Assistant.

    Giao thức: MQTT (coordinator.async_control_device) với payload JSON mã hóa AES.
    State đọc từ MQTT state (realtime) hoặc REST fallback.
    RestoreEntity: giữ mode/temp/fan sau restart HA (MQTT state chưa về).
    """

    _attr_hvac_modes = _ALL_HVAC_MODES
    _attr_fan_modes = _ALL_FAN_MODES
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = float(IR_TEMP_MIN)
    _attr_max_temp = float(IR_TEMP_MAX)
    _attr_target_temperature_step = 1.0
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))

        # Trạng thái nội bộ (restored hoặc optimistic)
        self._hvac_mode: HVACMode = HVACMode.OFF
        self._target_temp: float = float(IR_TEMP_DEFAULT)
        self._fan_mode: str = FAN_AUTO
        # Nhớ chế độ cuối trước khi tắt (để bật lại đúng)
        self._last_hvac_mode: HVACMode = HVACMode.COOL

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Khôi phục trạng thái lần trước (vì MQTT chưa về ngay sau restart)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        # Khôi phục HVAC mode
        if last.state in {m.value for m in _ALL_HVAC_MODES}:
            self._hvac_mode = HVACMode(last.state)
        attrs = last.attributes
        # Khôi phục nhiệt độ
        t = attrs.get("temperature")
        if t is not None:
            try:
                self._target_temp = max(IR_TEMP_MIN, min(IR_TEMP_MAX, float(t)))
            except (TypeError, ValueError):
                pass
        # Khôi phục fan mode
        fm = attrs.get("fan_mode")
        if fm in _FAN_LABEL_TO_CODE:
            self._fan_mode = fm
        # Lưu chế độ cuối (nếu không phải OFF)
        if self._hvac_mode != HVACMode.OFF:
            self._last_hvac_mode = self._hvac_mode

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def unique_id(self) -> str:
        return f"hunonic_climate_{self._device_id}"

    @property
    def name(self) -> str:
        return str(self._device.get("name", f"Điều hòa IR {self._device_id}"))

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

    # ── State helpers ─────────────────────────────────────────────────────────

    def _mqtt_state(self) -> dict[str, Any]:
        """MQTT realtime state (coordinator dict) của thiết bị này."""
        return self.coordinator.get_device_state(self._root_id)

    def _raw_value(self) -> dict[str, Any]:
        """Trả về REST `value` đã parse JSON (fallback khi chưa có MQTT state)."""
        raw = self.coordinator.get_device_raw(self._device_id)
        value = raw.get("value")
        if isinstance(value, str):
            try:
                v = json.loads(value)
                return v if isinstance(v, dict) else {}
            except (ValueError, TypeError):
                return {}
        if isinstance(value, dict):
            return value
        return {}

    def _get_field(self, *keys: str) -> Any:
        """Lần lượt đọc từ MQTT state → REST value → None."""
        state = self._mqtt_state()
        for k in keys:
            v = state.get(k)
            if v is not None:
                return v
        rv = self._raw_value()
        for k in keys:
            v = rv.get(k)
            if v is not None:
                return v
        return None

    def _is_on_from_state(self) -> bool | None:
        """True=bật, False=tắt, None=không rõ (dùng trạng thái restore)."""
        act = self._get_field("action")
        if act is not None:
            try:
                a = int(act)
                # action 1 = bật, 2 = tắt (Hunonic convention)
                if a == 1:
                    return True
                if a == 2:
                    return False
            except (TypeError, ValueError):
                pass
        return None

    def _mode_from_state(self) -> HVACMode:
        """Đọc HVACMode hiện tại từ MQTT/REST state."""
        on = self._is_on_from_state()
        if on is False:
            return HVACMode.OFF
        mode_raw = self._get_field("mode")
        if mode_raw is not None:
            try:
                return _MODE_TO_HVAC.get(int(mode_raw), HVACMode.COOL)
            except (TypeError, ValueError):
                pass
        # Nếu có action=1 nhưng không có mode → cool mặc định
        if on is True:
            return HVACMode.COOL
        return self._hvac_mode  # giữ restore

    def _temp_from_state(self) -> float:
        """Đọc nhiệt độ đặt từ MQTT/REST state."""
        t = self._get_field("temp", "temperature", "set_temp")
        if t is not None:
            try:
                return float(t)
            except (TypeError, ValueError):
                pass
        return self._target_temp

    def _fan_from_state(self) -> str:
        """Đọc fan mode từ MQTT/REST state."""
        f = self._get_field("fan", "fan_speed", "wind")
        if f is not None:
            try:
                return _FAN_CODE_TO_LABEL.get(int(f), self._fan_mode)
            except (TypeError, ValueError):
                pass
        return self._fan_mode

    # ── HA Climate properties ─────────────────────────────────────────────────

    @property
    def hvac_mode(self) -> HVACMode:
        """Chế độ HVAC hiện tại (đọc từ MQTT state realtime)."""
        mode = self._mode_from_state()
        # Cập nhật cache nội bộ để RestoreEntity lưu đúng
        self._hvac_mode = mode
        if mode != HVACMode.OFF:
            self._last_hvac_mode = mode
        return mode

    @property
    def target_temperature(self) -> float:
        t = self._temp_from_state()
        self._target_temp = t
        return t

    @property
    def fan_mode(self) -> str:
        fm = self._fan_from_state()
        self._fan_mode = fm
        return fm

    @property
    def current_temperature(self) -> float | None:
        """Nhiệt độ phòng hiện tại — nếu IR trả về (thường không có)."""
        t = self._get_field("current_temp", "room_temp", "ambient")
        if t is not None:
            try:
                return float(t)
            except (TypeError, ValueError):
                pass
        return None

    # ── Control actions ───────────────────────────────────────────────────────

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Đặt chế độ HVAC (bao gồm tắt = OFF)."""
        if hvac_mode == HVACMode.OFF:
            await self._send_off()
        else:
            mode_code = _HVAC_TO_MODE[hvac_mode]
            await self._send_on(
                mode=mode_code,
                temp=self._target_temp,
                fan=_FAN_LABEL_TO_CODE.get(self._fan_mode, IR_FAN_AUTO),
            )
            self._last_hvac_mode = hvac_mode
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

    @property
    def temperature_unit(self) -> str:
        """Luôn dùng độ C cho điều hòa."""
        return UnitOfTemperature.CELSIUS

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Đặt nhiệt độ mục tiêu."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp_val = float(temp)
        # Nếu HA gửi vào độ F (giá trị > 45), tự động đổi về độ C
        if temp_val > 45:
            temp_val = (temp_val - 32) * 5 / 9
        temp_val = max(float(IR_TEMP_MIN), min(float(IR_TEMP_MAX), float(round(temp_val))))
        self._target_temp = temp_val

        # Nếu đang tắt → bật kèm nhiệt độ mới (chế độ cuối)
        hvac = self._hvac_mode if self._hvac_mode != HVACMode.OFF else self._last_hvac_mode
        if hvac == HVACMode.OFF:
            hvac = HVACMode.COOL
        self._hvac_mode = hvac
        self.async_write_ha_state()

        await self._send_on(
            mode=_HVAC_TO_MODE.get(hvac, IR_MODE_COOL),
            temp=self._target_temp,
            fan=_FAN_LABEL_TO_CODE.get(self._fan_mode, IR_FAN_AUTO),
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Đặt tốc độ quạt."""
        self._fan_mode = fan_mode
        if self._hvac_mode == HVACMode.OFF:
            # Chỉ cập nhật cache, không gửi lệnh khi đang tắt
            self.async_write_ha_state()
            return
        hvac = self._hvac_mode
        await self._send_on(
            mode=_HVAC_TO_MODE.get(hvac, IR_MODE_COOL),
            temp=self._target_temp,
            fan=_FAN_LABEL_TO_CODE.get(fan_mode, IR_FAN_AUTO),
        )
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bật điều hòa ở chế độ cuối sử dụng."""
        hvac = self._last_hvac_mode
        await self._send_on(
            mode=_HVAC_TO_MODE.get(hvac, IR_MODE_COOL),
            temp=self._target_temp,
            fan=_FAN_LABEL_TO_CODE.get(self._fan_mode, IR_FAN_AUTO),
        )
        self._hvac_mode = hvac
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Tắt điều hòa."""
        await self._send_off()
        self._hvac_mode = HVACMode.OFF
        self.async_write_ha_state()

    # ── MQTT helpers ──────────────────────────────────────────────────────────

    @property
    def _uid(self) -> int:
        try:
            return int(self.coordinator._user_id or 0)
        except (TypeError, ValueError):
            return 0

    async def _send_on(self, mode: int, temp: float, fan: int) -> None:
        """Gửi lệnh BẬT với chế độ, nhiệt độ, tốc độ quạt."""
        payload: dict[str, Any] = {
            "u": self._uid,
            self._root_type: 0,
            "act_id": 0,
            "action": 1,
            "mode": mode,
            "temp": int(temp),
            "fan": fan,
            "src": 1,
        }
        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug(
            "IR AC %s → ON mode=%s temp=%s fan=%s",
            self._device.get("name"), mode, int(temp), fan,
        )

    async def _send_off(self) -> None:
        """Gửi lệnh TẮT."""
        payload: dict[str, Any] = {
            "u": self._uid,
            self._root_type: 0,
            "act_id": 0,
            "action": 2,
            "src": 1,
        }
        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("IR AC %s → OFF", self._device.get("name"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Thêm thông tin chi tiết vào attributes."""
        state = self._mqtt_state()
        return {
            "device_type": self._root_type,
            "root_id": self._root_id,
            "raw_action": state.get("action"),
            "raw_mode": state.get("mode"),
            "raw_fan": state.get("fan"),
            "raw_temp": state.get("temp"),
        }
