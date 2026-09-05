"""Climate entity cho thiết bị IR điều hòa Hunonic (Home Assistant).

Hỗ trợ:
- Bật/tắt qua HVACMode.OFF
- Chế độ: Auto / Cool / Heat / Dry / Fan Only
- Nhiệt độ đặt: 16°C – 30°C (bước 1°C)
- Tốc độ quạt: auto / low / medium / high
- Cánh vẫy (Swing): Tắt, Vẫy dọc, Vẫy ngang, Cả hai (Điều hòa T4)
- Profile tùy chỉnh chuẩn xác:
    + Điều hòa T2: Midea MSAFG-13CRN8 (remote 227)
    + Điều hòa T4: Daikin (remote 226) với vẫy dọc + ngang
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
    IR_TEMP_DEFAULT,
    IR_TEMP_MAX,
    IR_TEMP_MIN,
)
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)

# Fan speed labels
FAN_AUTO = "auto"
FAN_LOW = "low"
FAN_MEDIUM = "medium"
FAN_HIGH = "high"

_ALL_FAN_MODES = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]
_ALL_HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.AUTO,
    HVACMode.COOL,
    HVACMode.HEAT,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]

SWING_OFF = "Tắt"
SWING_VERTICAL = "Vẫy dọc"
SWING_HORIZONTAL = "Vẫy ngang"
SWING_BOTH = "Cả hai"
_ALL_SWING_MODES = [SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH]

# Default standard profiles
# 1. Midea MSAFG-13CRN8 (Điều hòa T2 - remote 227):
# Mode: auto=2, dry=2, cool=0, fan=4, heat=3
# Fan: min(low)=1, med=2, max(high)=3, auto=0
MIDEA_HVAC_TO_CODE = {
    HVACMode.AUTO: 2,
    HVACMode.COOL: 0,
    HVACMode.DRY: 2,
    HVACMode.FAN_ONLY: 4,
    HVACMode.HEAT: 3,
}
MIDEA_FAN_TO_CODE = {
    FAN_AUTO: 0,
    FAN_LOW: 1,
    FAN_MEDIUM: 2,
    FAN_HIGH: 3,
}

# 2. Daikin (Điều hòa T4 - remote 226):
# Mode: auto=0, dry=2, cool=3, fan=6, heat=4
# Fan: min(low)=1, med=3, max(high)=5, auto=10
# Swing: swingv (auto=15, off=0), swingh (auto=15, off=0)
DAIKIN_HVAC_TO_CODE = {
    HVACMode.AUTO: 0,
    HVACMode.COOL: 3,
    HVACMode.DRY: 2,
    HVACMode.FAN_ONLY: 6,
    HVACMode.HEAT: 4,
}
DAIKIN_FAN_TO_CODE = {
    FAN_AUTO: 10,
    FAN_LOW: 1,
    FAN_MEDIUM: 3,
    FAN_HIGH: 5,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập climate entities IR."""
    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        if device.get("root_type") in IR_AC_TYPES:
            name = str(device.get("name", "")).upper()
            if "QUẠT" in name and "ĐIỀU" not in name:
                return []
            return [HunonicIRClimate(coordinator, device)]
        return []

    setup_entities(hass, entry, async_add_entities, _build)


class HunonicIRClimate(CoordinatorEntity[HunonicCoordinator], ClimateEntity, RestoreEntity):
    """Điều hòa IR Hunonic cho Home Assistant."""

    _attr_hvac_modes = _ALL_HVAC_MODES
    _attr_fan_modes = _ALL_FAN_MODES
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))

        self._attr_min_temp = float(IR_TEMP_MIN)
        self._attr_max_temp = float(IR_TEMP_MAX)

        # Profile cấu hình lệnh
        self._hvac_to_code: dict[HVACMode, int] = dict(MIDEA_HVAC_TO_CODE)
        self._fan_to_code: dict[str, int] = dict(MIDEA_FAN_TO_CODE)
        self._has_swing_v: bool = False
        self._has_swing_h: bool = False

        self._parse_device_remote()

        # Features
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self._has_swing_v or self._has_swing_h:
            features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = _ALL_SWING_MODES
        else:
            self._attr_swing_modes = None

        self._attr_supported_features = features

        # Trạng thái nội bộ
        self._hvac_mode: HVACMode = HVACMode.OFF
        self._target_temp: float = float(IR_TEMP_DEFAULT)
        self._fan_mode: str = FAN_AUTO
        self._swing_mode: str = SWING_OFF
        self._last_hvac_mode: HVACMode = HVACMode.COOL

    def _parse_device_remote(self) -> None:
        """Phân tích cấu trúc remote của thiết bị để nạp mã lệnh chính xác."""
        dev_name = str(self._device.get("name", "")).upper()
        dev_id = str(self._device.get("id", ""))

        if "T2" in dev_name or "MIDEA" in dev_name or dev_id in ("3525534", "2941402"):
            self._hvac_to_code = dict(MIDEA_HVAC_TO_CODE)
            self._fan_to_code = dict(MIDEA_FAN_TO_CODE)
            self._attr_min_temp = 16.0
            self._attr_max_temp = 30.0
            self._has_swing_v = False
            self._has_swing_h = False
            return

        if "T4" in dev_name or "DAIKIN" in dev_name or dev_id == "3488246":
            self._hvac_to_code = dict(DAIKIN_HVAC_TO_CODE)
            self._fan_to_code = dict(DAIKIN_FAN_TO_CODE)
            self._attr_min_temp = 16.0
            self._attr_max_temp = 30.0
            self._has_swing_v = True
            self._has_swing_h = True
            return

        rem = self._device.get("remote")
        if isinstance(rem, list):
            for item in rem:
                kname = str(item.get("key_name", "")).lower()
                kval = item.get("key_value", "")
                if kname == "temp_min" and kval:
                    try:
                        self._attr_min_temp = float(kval)
                    except ValueError:
                        pass
                elif kname == "temp_max" and kval:
                    try:
                        self._attr_max_temp = float(kval)
                    except ValueError:
                        pass
                elif kname == "mode" and kval:
                    try:
                        m_list = json.loads(kval)
                        if isinstance(m_list, list):
                            for m in m_list:
                                mn = str(m.get("name", "")).lower()
                                mc = int(m.get("code", 0))
                                if mn == "cool":
                                    self._hvac_to_code[HVACMode.COOL] = mc
                                elif mn == "heat":
                                    self._hvac_to_code[HVACMode.HEAT] = mc
                                elif mn == "dry":
                                    self._hvac_to_code[HVACMode.DRY] = mc
                                elif mn == "fan":
                                    self._hvac_to_code[HVACMode.FAN_ONLY] = mc
                                elif mn == "auto":
                                    self._hvac_to_code[HVACMode.AUTO] = mc
                    except Exception:
                        pass
                elif kname == "fan" and kval:
                    try:
                        f_list = json.loads(kval)
                        if isinstance(f_list, list):
                            for f in f_list:
                                fn = str(f.get("name", "")).lower()
                                fc = int(f.get("code", 0))
                                if fn in ("min", "low", "1"):
                                    self._fan_to_code[FAN_LOW] = fc
                                elif fn in ("med", "medium", "2"):
                                    self._fan_to_code[FAN_MEDIUM] = fc
                                elif fn in ("max", "high", "3"):
                                    self._fan_to_code[FAN_HIGH] = fc
                                elif fn in ("auto", "0"):
                                    self._fan_to_code[FAN_AUTO] = fc
                    except Exception:
                        pass
                elif kname == "swingv" and kval:
                    self._has_swing_v = True
                elif kname == "swingh" and kval:
                    self._has_swing_h = True

    async def async_added_to_hass(self) -> None:
        """Khôi phục trạng thái lần trước."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        if last.state in {m.value for m in _ALL_HVAC_MODES}:
            self._hvac_mode = HVACMode(last.state)
        attrs = last.attributes
        t = attrs.get("temperature")
        if t is not None:
            try:
                self._target_temp = max(self._attr_min_temp, min(self._attr_max_temp, float(t)))
            except (TypeError, ValueError):
                pass
        fm = attrs.get("fan_mode")
        if fm in _ALL_FAN_MODES:
            self._fan_mode = fm
        sm = attrs.get("swing_mode")
        if sm in _ALL_SWING_MODES:
            self._swing_mode = sm
        if self._hvac_mode != HVACMode.OFF:
            self._last_hvac_mode = self._hvac_mode

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

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def target_temperature(self) -> float:
        return self._target_temp

    @property
    def fan_mode(self) -> str:
        return self._fan_mode

    @property
    def swing_mode(self) -> str | None:
        if self._has_swing_v or self._has_swing_h:
            return self._swing_mode
        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Đặt chế độ HVAC."""
        if hvac_mode == HVACMode.OFF:
            await self._send_off()
        else:
            await self._send_on(
                hvac_mode=hvac_mode,
                temp=self._target_temp,
                fan_mode=self._fan_mode,
                swing_mode=self._swing_mode,
            )
            self._last_hvac_mode = hvac_mode
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Đặt nhiệt độ mục tiêu."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp_val = float(temp)
        if temp_val > 45:
            temp_val = (temp_val - 32) * 5 / 9
        temp_val = max(self._attr_min_temp, min(self._attr_max_temp, float(round(temp_val))))
        self._target_temp = temp_val

        hvac = self._hvac_mode if self._hvac_mode != HVACMode.OFF else self._last_hvac_mode
        if hvac == HVACMode.OFF:
            hvac = HVACMode.COOL
        self._hvac_mode = hvac

        await self._send_on(
            hvac_mode=hvac,
            temp=self._target_temp,
            fan_mode=self._fan_mode,
            swing_mode=self._swing_mode,
        )
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Đặt tốc độ quạt."""
        if fan_mode in _ALL_FAN_MODES:
            self._fan_mode = fan_mode
            if self._hvac_mode != HVACMode.OFF:
                await self._send_on(
                    hvac_mode=self._hvac_mode,
                    temp=self._target_temp,
                    fan_mode=fan_mode,
                    swing_mode=self._swing_mode,
                )
            self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Đặt cánh vẫy gió."""
        if swing_mode in _ALL_SWING_MODES:
            self._swing_mode = swing_mode
            if self._hvac_mode != HVACMode.OFF:
                await self._send_on(
                    hvac_mode=self._hvac_mode,
                    temp=self._target_temp,
                    fan_mode=self._fan_mode,
                    swing_mode=swing_mode,
                )
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bật điều hòa."""
        hvac = self._last_hvac_mode if self._last_hvac_mode != HVACMode.OFF else HVACMode.COOL
        self._hvac_mode = hvac
        await self._send_on(
            hvac_mode=hvac,
            temp=self._target_temp,
            fan_mode=self._fan_mode,
            swing_mode=self._swing_mode,
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Tắt điều hòa."""
        await self._send_off()
        self._hvac_mode = HVACMode.OFF
        self.async_write_ha_state()

    @property
    def _uid(self) -> int:
        try:
            return int(self.coordinator._user_id or 0)
        except (TypeError, ValueError):
            return 0

    async def _send_on(
        self,
        hvac_mode: HVACMode,
        temp: float,
        fan_mode: str,
        swing_mode: str,
    ) -> None:
        """Gửi lệnh bật điều hòa kèm chế độ, nhiệt độ, quạt, cánh vẫy."""
        t_int = int(round(temp))
        mode_code = self._hvac_to_code.get(hvac_mode, 0)
        fan_code = self._fan_to_code.get(fan_mode, 0)

        payload: dict[str, Any] = {
            "u": self._uid,
            self._root_type: 0,
            "act_id": 0,
            "action": 1,
            "mode": mode_code,
            "temp": t_int,
            "fan": fan_code,
            "src": 1,
        }

        if self._has_swing_v:
            payload["swingv"] = 15 if swing_mode in (SWING_VERTICAL, SWING_BOTH) else 0
        if self._has_swing_h:
            payload["swingh"] = 15 if swing_mode in (SWING_HORIZONTAL, SWING_BOTH) else 0

        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("Gửi lệnh điều hòa ON tới %s: %s", self._device.get("name"), payload)

    async def _send_off(self) -> None:
        """Gửi lệnh tắt điều hòa."""
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
        _LOGGER.debug("Gửi lệnh điều hòa OFF tới %s: %s", self._device.get("name"), payload)
