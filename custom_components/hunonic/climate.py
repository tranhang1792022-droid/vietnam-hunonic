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

def _is_ac_device(device: dict[str, Any]) -> bool:
    """Chỉ tạo Climate cho điều hòa thực thụ (AC). Không tạo cho quạt hay TV, đèn."""
    cat = device.get("category")
    if isinstance(cat, dict):
        cat_name = str(cat.get("name_en") or cat.get("name") or "").upper()
        cat_id = str(cat.get("id") or "")
        if cat_name == "AC" or cat_id == "1":
            return True
        if cat_name in ("CUSTOM_USER", "CUSTOM_RF", "TV", "LIGHT", "FAN"):
            return False

    dev_name = str(device.get("name") or "").upper()
    if any(k in dev_name for k in ("ĐIỀU HÒA", "ĐIỀU HOÀ", "DIEU HOA", "AIR CONDITION", "CLIMATE")):
        if "QUẠT" not in dev_name and "FAN" not in dev_name and "TIVI" not in dev_name:
            return True

    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập climate entities IR (tự thêm thiết bị mới khi danh sách thay đổi)."""

    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        if device.get("root_type") in IR_AC_TYPES and _is_ac_device(device):
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

        # Bảng mã mode & fan riêng cho từng thiết bị dựa theo remote profile từ Hunonic
        self._hvac_to_mode: dict[HVACMode, int] = dict(_HVAC_TO_MODE)
        self._mode_to_hvac: dict[int, HVACMode] = dict(_MODE_TO_HVAC)
        self._fan_label_to_code: dict[str, int] = dict(_FAN_LABEL_TO_CODE)
        self._fan_code_to_label: dict[int, str] = dict(_FAN_CODE_TO_LABEL)
        self._parse_remote_profile()

        # Khởi tạo trạng thái ban đầu từ REST value nếu có
        rv = self._raw_value()
        if rv:
            p = rv.get("power")
            if p is not None:
                try:
                    self._hvac_mode = HVACMode.COOL if int(p) == 1 else HVACMode.OFF
                except (ValueError, TypeError):
                    pass
            t = rv.get("temp")
            if t is not None:
                try:
                    self._target_temp = float(t)
                except (ValueError, TypeError):
                    pass
            m = rv.get("mode")
            if m is not None:
                try:
                    mapped_m = self._mode_to_hvac.get(int(m))
                    if mapped_m:
                        self._last_hvac_mode = mapped_m
                        if self._hvac_mode != HVACMode.OFF:
                            self._hvac_mode = mapped_m
                except (ValueError, TypeError):
                    pass
            f = rv.get("fan")
            if f is not None:
                try:
                    mapped_f = self._fan_code_to_label.get(int(f))
                    if mapped_f:
                        self._fan_mode = mapped_f
                except (ValueError, TypeError):
                    pass

    def _parse_remote_profile(self) -> None:
        """Đọc bảng mã mode/fan/temp từ remote profile của chính thiết bị (Daikin, Funiki...)."""
        rem = self._device.get("remote")
        if not rem or not isinstance(rem, list):
            return

        for item in rem:
            if not isinstance(item, dict):
                continue
            k_name = str(item.get("key_name") or "").strip().lower()
            k_val = str(item.get("key_value") or "").strip()

            if k_name == "temp_min":
                try:
                    self._attr_min_temp = float(k_val)
                except (ValueError, TypeError):
                    pass
            elif k_name == "temp_max":
                try:
                    self._attr_max_temp = float(k_val)
                except (ValueError, TypeError):
                    pass
            elif k_name == "mode":
                try:
                    modes_list = json.loads(k_val)
                    if isinstance(modes_list, list):
                        for m in modes_list:
                            m_name = str(m.get("name") or "").lower()
                            m_code = m.get("code")
                            if m_code is not None:
                                c = int(m_code)
                                if m_name == "cool":
                                    self._hvac_to_mode[HVACMode.COOL] = c
                                elif m_name == "heat":
                                    self._hvac_to_mode[HVACMode.HEAT] = c
                                elif m_name == "dry":
                                    self._hvac_to_mode[HVACMode.DRY] = c
                                elif m_name == "fan":
                                    self._hvac_to_mode[HVACMode.FAN_ONLY] = c
                                elif m_name == "auto":
                                    self._hvac_to_mode[HVACMode.AUTO] = c
                        self._mode_to_hvac = {v: k for k, v in self._hvac_to_mode.items()}
                except Exception:
                    pass
            elif k_name == "fan":
                try:
                    fans_list = json.loads(k_val)
                    if isinstance(fans_list, list):
                        for f in fans_list:
                            f_name = str(f.get("name") or "").lower()
                            f_code = f.get("code")
                            if f_code is not None:
                                c = int(f_code)
                                if f_name == "auto":
                                    self._fan_label_to_code[FAN_AUTO] = c
                                elif f_name == "min":
                                    self._fan_label_to_code[FAN_MIN] = c
                                elif f_name in ("low", "med_low"):
                                    self._fan_label_to_code[FAN_LOW] = c
                                elif f_name in ("med", "medium"):
                                    self._fan_label_to_code[FAN_MEDIUM] = c
                                elif f_name in ("high", "med_high"):
                                    self._fan_label_to_code[FAN_HIGH] = c
                                elif f_name == "max":
                                    self._fan_label_to_code[FAN_MAX] = c
                        self._fan_code_to_label = {v: k for k, v in self._fan_label_to_code.items()}
                except Exception:
                    pass

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
        for rid in filter(None, [self._device_id, self._root_id]):
            st = self.coordinator.get_device_state(rid)
            if st:
                return st
        hub = self.coordinator.find_parent_irwifi(self._device)
        if hub:
            hub_rid = str(hub.get("root_id", ""))
            if hub_rid:
                return self.coordinator.get_device_state(hub_rid)
        return {}

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
        act = self._get_field("action", "power")
        if act is not None:
            try:
                a = int(act)
                # action 1 / power 1 = bật, action 2 / power 0 = tắt
                if a == 1:
                    return True
                if a in (0, 2):
                    return False
            except (TypeError, ValueError):
                pass
        return None

    def _mode_from_state(self) -> HVACMode:
        """Đọc HVACMode hiện tại từ MQTT/REST state theo map động của thiết bị."""
        on = self._is_on_from_state()
        if on is False:
            return HVACMode.OFF
        mode_raw = self._get_field("mode")
        if mode_raw is not None:
            try:
                return self._mode_to_hvac.get(int(mode_raw), HVACMode.COOL)
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
        """Đọc fan mode từ MQTT/REST state theo map động của thiết bị."""
        f = self._get_field("fan", "fan_speed", "wind")
        if f is not None:
            try:
                return self._fan_code_to_label.get(int(f), self._fan_mode)
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
            mode_code = self._hvac_to_mode.get(hvac_mode, IR_MODE_COOL)
            await self._send_on(
                mode=mode_code,
                temp=self._target_temp,
                fan=self._fan_label_to_code.get(self._fan_mode, IR_FAN_AUTO),
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
            mode=self._hvac_to_mode.get(hvac, IR_MODE_COOL),
            temp=self._target_temp,
            fan=self._fan_label_to_code.get(self._fan_mode, IR_FAN_AUTO),
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
            mode=self._hvac_to_mode.get(hvac, IR_MODE_COOL),
            temp=self._target_temp,
            fan=self._fan_label_to_code.get(fan_mode, IR_FAN_AUTO),
        )
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bật điều hòa ở chế độ cuối sử dụng."""
        hvac = self._last_hvac_mode
        await self._send_on(
            mode=self._hvac_to_mode.get(hvac, IR_MODE_COOL),
            temp=self._target_temp,
            fan=self._fan_label_to_code.get(self._fan_mode, IR_FAN_AUTO),
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
        t_int = int(round(temp))
        self._target_temp = float(t_int)
        self._fan_mode = self._fan_code_to_label.get(fan, self._fan_mode)
        self._hvac_mode = self._mode_to_hvac.get(mode, HVACMode.COOL)
        self._last_hvac_mode = self._hvac_mode

        # Cập nhật optimistic vào coordinator ngay lập tức
        st = {
            "action": 1,
            "power": 1,
            "mode": mode,
            "temp": t_int,
            "fan": fan,
        }
        self.coordinator.update_device_state(self._device_id, st)
        self.coordinator.update_device_state(self._root_id, st)

        payload: dict[str, Any] = {
            "u": self._uid,
            self._root_type: 0,
            "act_id": 0,
            "action": 1,
            "mode": mode,
            "temp": t_int,
            "fan": fan,
            "src": 1,
        }
        # Đính kèm brand id (Daikin 14, Funiki 104...) và swing
        b_info = self._device.get("brand")
        if isinstance(b_info, dict) and b_info.get("id"):
            try:
                payload["brand"] = int(b_info["id"])
            except (ValueError, TypeError):
                payload["brand"] = b_info["id"]

        for sw in ("swingv", "swingh"):
            sw_val = self._get_field(sw)
            if sw_val is not None:
                try:
                    payload[sw] = int(sw_val)
                except (ValueError, TypeError):
                    pass

        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        self.async_write_ha_state()
        _LOGGER.debug(
            "IR AC %s → ON mode=%s temp=%s fan=%s",
            self._device.get("name"), mode, t_int, fan,
        )

    async def _send_off(self) -> None:
        """Gửi lệnh TẮT."""
        self._hvac_mode = HVACMode.OFF
        st = {"action": 2, "power": 0}
        self.coordinator.update_device_state(self._device_id, st)
        self.coordinator.update_device_state(self._root_id, st)

        payload: dict[str, Any] = {
            "u": self._uid,
            self._root_type: 0,
            "act_id": 0,
            "action": 2,
            "src": 1,
        }
        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        self.async_write_ha_state()
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
