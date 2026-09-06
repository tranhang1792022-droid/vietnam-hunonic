"""Sensor trạng thái thiết bị Hunonic cho Home Assistant."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

try:
    from homeassistant.const import UnitOfTemperature
    _TEMP_CELSIUS = UnitOfTemperature.CELSIUS
except ImportError:
    _TEMP_CELSIUS = "°C"

try:
    from homeassistant.const import UnitOfEnergy, UnitOfPower
except ImportError:
    class UnitOfEnergy:  # type: ignore
        KILO_WATT_HOUR = "kWh"

    class UnitOfPower:  # type: ignore
        WATT = "W"

from .const import DOMAIN, DOOR_TYPES, GATE_HUB_TYPES, GATE_TYPES, IR_AC_TYPES, METER_TYPES, TH_TYPES
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)

# Root types có thể đo công suất điện
_POWER_MEASURE_TYPES = frozenset({"wsm", "swinput", "swinputv2"})

# Root types cổng/cửa để tạo sensor trạng thái riêng
_COVER_TYPES = frozenset(GATE_HUB_TYPES + GATE_TYPES + DOOR_TYPES)

# Root types công tơ điện
_METER_TYPES = frozenset(METER_TYPES)

# Root types điều hòa IR
_IR_AC_TYPES = frozenset(IR_AC_TYPES)

# Root types cảm biến nhiệt độ & độ ẩm
_TH_TYPES = frozenset(t.lower() for t in TH_TYPES)


def _is_th_sensor(device: Any) -> bool:
    """Kiểm tra thiết bị có phải cảm biến nhiệt độ & độ ẩm (thswifi, GACHths, ...)."""
    if isinstance(device, str):
        dev_dict = {"root_type": device}
    elif isinstance(device, dict):
        dev_dict = device
    else:
        return False

    root_type = str(dev_dict.get("root_type") or "").lower().strip()
    dev_type = str(dev_dict.get("type") or "").lower().strip()
    root_id = str(dev_dict.get("root_id") or "").lower().strip()
    model = str(dev_dict.get("model") or "").lower().strip()
    name = str(dev_dict.get("name") or "").lower().strip()
    topic = str(dev_dict.get("topicpub") or dev_dict.get("topicsub") or "").lower().strip()

    if root_type in _TH_TYPES or dev_type in _TH_TYPES or model in _TH_TYPES:
        return True

    th_keywords = (
        "thswifi", "thswifiv2", "thwifi", "thsensor", "gach_ths",
        "gachths", "sensortemp", "thwswifi", "swth", "ths",
    )
    for kw in th_keywords:
        if kw in root_type or kw in dev_type or kw in root_id or kw in model or kw in topic:
            return True

    # Quét toàn bộ chuỗi dev_dict để nhận diện bất kỳ cấu trúc trả về nào
    try:
        raw_str = json.dumps(dev_dict, ensure_ascii=False).lower()
        for kw in ("thswifi", "thwifi", "thsensor", "gachths", "gach_ths", "sensortemp"):
            if kw in raw_str:
                return True
    except Exception:
        pass

    # Kiểm tra value có chứa temp hoặc humi
    val = dev_dict.get("value")
    if isinstance(val, dict) and any(k in val for k in ("temp", "humi", "temperature", "humidity", "t", "h")):
        return True
    if isinstance(val, str) and any(k in val.lower() for k in ('"temp"', '"humi"', '"temperature"', '"humidity"', 'temp', 'humi')):
        return True

    # Kiểm tra data_extra hoặc root_extra
    extra = dev_dict.get("data_extra") or dev_dict.get("root_extra")
    if isinstance(extra, dict) and any(k in extra for k in ("temp", "humi", "temperature", "humidity")):
        return True
    elif isinstance(extra, str) and any(k in extra.lower() for k in ('"temp"', '"humi"', '"temperature"', '"humidity"', 'temp', 'humi')):
        return True

    if dev_dict.get("temp") is not None or dev_dict.get("humi") is not None:
        return True

    # Kiểm tra tên thiết bị nếu có từ khóa cảm biến nhiệt ẩm
    if any(k in name for k in ("thswifi", "cảm biến", "nhiệt", "ẩm", "temp", "humi", "sensor")):
        # Loại trừ các thiết bị không phải cảm biến môi trường
        if not any(sw in root_type for sw in ("switch", "door", "gate", "camera", "irchild", "rfchild", "fan", "light")):
            return True

    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập sensor entities (tự thêm thiết bị mới khi danh sách thay đổi)."""
    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        root_type: str = device.get("root_type", "")
        ents: list[SensorEntity] = [
            # Mọi thiết bị đều có sensor kết nối online/offline
            HunonicConnectivitySensor(coordinator, device)
        ]
        if root_type in _COVER_TYPES:
            ents.append(HunonicCoverPositionSensor(coordinator, device))
        # Công suất tức thời (Watt): thiết bị đo điện chuyên dụng + công tơ (meter
        # đọc từ data_extra.power_current).
        if root_type in _POWER_MEASURE_TYPES or root_type in _METER_TYPES:
            ents.append(HunonicPowerSensor(coordinator, device))
        if root_type in _METER_TYPES:
            ents.append(HunonicMeterEnergySensor(coordinator, device, prev=False))
            ents.append(HunonicMeterEnergySensor(coordinator, device, prev=True))
            ents.append(HunonicMeterCostSensor(coordinator, device, prev=False))
            ents.append(HunonicMeterCostSensor(coordinator, device, prev=True))
        # Sensor trạng thái điều hòa IR (chế độ, nhiệt độ đặt, tốc độ quạt).
        if root_type in _IR_AC_TYPES:
            ents.append(HunonicACModeSensor(coordinator, device))
            ents.append(HunonicACTempSensor(coordinator, device))
            ents.append(HunonicACFanSensor(coordinator, device))
        # Cảm biến nhiệt độ & độ ẩm (thswifi, thwifi, ...)
        if _is_th_sensor(device):
            ents.append(HunonicTemperatureSensor(coordinator, device))
            ents.append(HunonicHumiditySensor(coordinator, device))
            ents.append(HunonicBatterySensor(coordinator, device))
        # Sensor chẩn đoán cấp THIẾT BỊ (chung mọi nút) — chỉ tạo 1 lần ở kênh 1.
        if str(device.get("index_in_root", "1")) == "1":
            ents.append(HunonicFirmwareSensor(coordinator, device))
            ents.append(HunonicMacSensor(coordinator, device))
            ents.append(HunonicOfflineNotifySensor(coordinator, device))
        return ents

    setup_entities(hass, entry, async_add_entities, _build)



class _HunonicSensorBase(CoordinatorEntity[HunonicCoordinator], SensorEntity):
    """Base class dùng chung cho sensor Hunonic."""

    def __init__(self, coordinator: HunonicCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))

    @property
    def available(self) -> bool:
        """Sensor Hunonic luôn available nếu device tồn tại, tránh Unavailable khi mạng lag/sleep."""
        return bool(self._device)

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


class HunonicConnectivitySensor(_HunonicSensorBase):
    """Sensor theo dõi trạng thái online/offline của thiết bị."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]
    _attr_icon = "mdi:lan-connect"

    @property
    def available(self) -> bool:
        """Sensor kết nối luôn available để báo online/offline chính xác."""
        return True

    @property
    def unique_id(self) -> str:
        return f"hunonic_sensor_{self._device_id}_status"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        return f"{device_name} - Kết nối"

    @property
    def native_value(self) -> str:
        # Dùng is_device_online (field `state` — đã kiểm chứng đáng tin).
        return "online" if self.coordinator.is_device_online(self._device_id) else "offline"

    @property
    def icon(self) -> str:
        return (
            "mdi:lan-connect"
            if self.native_value == "online"
            else "mdi:lan-disconnect"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Thêm thông tin thiết bị vào attributes."""
        raw = self.coordinator.get_device_raw(self._device_id)
        return {
            "device_type": self._root_type,
            "root_id": self._root_id,
            "fw_version": raw.get("fw_version", raw.get("firmware", "")),
            "ip_address": raw.get("ip", raw.get("ip_address", "")),
        }


class HunonicCoverPositionSensor(_HunonicSensorBase):
    """Sensor hiển thị vị trí (%) của cổng/cửa."""

    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:garage"

    @property
    def unique_id(self) -> str:
        return f"hunonic_sensor_{self._device_id}_position"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        return f"{device_name} - Vị trí"

    @property
    def native_value(self) -> int | None:
        """Vị trí hiện tại (0=đóng, 100=mở hoàn toàn)."""
        state = self.coordinator.get_device_state(self._root_id)
        for key in ("pcn", "position", "pos"):
            val = state.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass

        raw = self.coordinator.get_device_raw(self._device_id)
        for key in ("pcn", "position"):
            val = raw.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
        return None

    @property
    def icon(self) -> str:
        val = self.native_value
        if val is None:
            return "mdi:garage-alert"
        if val == 0:
            return "mdi:garage"
        if val == 100:
            return "mdi:garage-open"
        return "mdi:garage-variant"


class HunonicPowerSensor(_HunonicSensorBase):
    """Sensor đo công suất tiêu thụ (Watt) cho các thiết bị đo điện."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:flash"

    @property
    def unique_id(self) -> str:
        return f"hunonic_sensor_{self._device_id}_power"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        return f"{device_name} - Công suất"

    @property
    def native_value(self) -> float | None:
        """Công suất tiêu thụ tính bằng Watt."""
        state = self.coordinator.get_device_state(self._root_id)
        for key in ("power", "watt", "w", "p"):
            val = state.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass

        raw = self.coordinator.get_device_raw(self._device_id)
        for key in ("power", "watt", "w", "power_current"):
            val = raw.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass

        # Công tơ (atmwifi/elmeter): công suất tức thời nằm trong data_extra.
        data_extra = raw.get("data_extra")
        if isinstance(data_extra, dict):
            val = data_extra.get("power_current")
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return None


class _HunonicMeterBase(_HunonicSensorBase):
    """Base cho sensor công tơ điện — đọc số liệu từ field `root_extra` (REST).

    `root_extra` (chuỗi JSON) chứa: power_of_month, money_of_month,
    power_of_prev_month, money_of_prev_month. Poll lại mỗi chu kỳ coordinator.
    """

    def _root_extra(self) -> dict[str, Any]:
        raw = self.coordinator.get_device_raw(self._device_id)
        extra = raw.get("root_extra")
        if isinstance(extra, str):
            try:
                return json.loads(extra)
            except (ValueError, TypeError):
                return {}
        return extra if isinstance(extra, dict) else {}


class HunonicMeterEnergySensor(_HunonicMeterBase):
    """Điện năng tiêu thụ tháng này / tháng trước (kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self, coordinator: HunonicCoordinator, device: dict[str, Any], prev: bool
    ) -> None:
        super().__init__(coordinator, device)
        self._prev = prev
        self._key = "power_of_prev_month" if prev else "power_of_month"

    @property
    def unique_id(self) -> str:
        suffix = "energy_prev_month" if self._prev else "energy_month"
        return f"hunonic_sensor_{self._device_id}_{suffix}"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        label = "Điện năng tháng trước" if self._prev else "Điện năng tháng này"
        return f"{device_name} - {label}"

    @property
    def native_value(self) -> float | None:
        val = self._root_extra().get(self._key)
        if val is None:
            return None
        try:
            return round(float(val), 2)
        except (TypeError, ValueError):
            return None


class HunonicMeterCostSensor(_HunonicMeterBase):
    """Tiền điện tháng này / tháng trước (VND)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "VND"
    _attr_icon = "mdi:cash"

    def __init__(
        self, coordinator: HunonicCoordinator, device: dict[str, Any], prev: bool
    ) -> None:
        super().__init__(coordinator, device)
        self._prev = prev
        self._key = "money_of_prev_month" if prev else "money_of_month"

    @property
    def unique_id(self) -> str:
        suffix = "cost_prev_month" if self._prev else "cost_month"
        return f"hunonic_sensor_{self._device_id}_{suffix}"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        label = "Tiền điện tháng trước" if self._prev else "Tiền điện tháng này"
        return f"{device_name} - {label}"

    @property
    def native_value(self) -> int | None:
        val = self._root_extra().get(self._key)
        if val is None:
            return None
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return None


# ── Sensor chẩn đoán cấp thiết bị (Thông tin chung / cấu hình — read-only) ──────
# Lấy từ field API của thiết bị (per root_id). Phần GHI (đổi cấu hình) cần MITM.

class _HunonicDiagBase(_HunonicSensorBase):
    """Base cho sensor chẩn đoán (đặt vào nhóm 'Chẩn đoán')."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC


class HunonicFirmwareSensor(_HunonicDiagBase):
    """Phiên bản phần cứng/firmware (field `version`)."""

    _attr_icon = "mdi:chip"

    @property
    def unique_id(self) -> str:
        return f"hunonic_{self._root_id}_fw"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', self._device_id)} - Phiên bản"

    @property
    def native_value(self) -> str | None:
        raw = self.coordinator.get_device_raw(self._device_id)
        v = raw.get("version")
        return str(v) if v not in (None, "") else None


class HunonicMacSensor(_HunonicDiagBase):
    """Địa chỉ MAC Bluetooth (root_extra.mac_bt)."""

    _attr_icon = "mdi:bluetooth"

    @property
    def unique_id(self) -> str:
        return f"hunonic_{self._root_id}_mac_bt"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', self._device_id)} - MAC Bluetooth"

    @property
    def native_value(self) -> str | None:
        raw = self.coordinator.get_device_raw(self._device_id)
        extra = raw.get("root_extra")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (ValueError, TypeError):
                extra = {}
        if isinstance(extra, dict):
            return extra.get("mac_bt") or None
        return None


class HunonicOfflineNotifySensor(_HunonicDiagBase):
    """Thông báo khi thiết bị mất kết nối (field `notify_offline`)."""

    _attr_icon = "mdi:bell-alert"

    @property
    def unique_id(self) -> str:
        return f"hunonic_{self._root_id}_notify_offline"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', self._device_id)} - Thông báo offline"

    @property
    def native_value(self) -> str:
        raw = self.coordinator.get_device_raw(self._device_id)
        return "Bật" if str(raw.get("notify_offline", "0")) == "1" else "Tắt"


# ── Sensor điều hòa IR ────────────────────────────────────────────────────────

_IR_MODE_LABELS: dict[int, str] = {
    0: "Auto",
    1: "Cool",
    2: "Dry",
    3: "Fan",
    4: "Heat",
}

_IR_FAN_LABELS: dict[int, str] = {
    0: "Auto",
    1: "Min",
    2: "Low",
    3: "Medium",
    4: "High",
    5: "Max",
}


class _HunonicACBase(_HunonicDiagBase):
    """Base sensor chẩn đoán cho điều hòa IR — đọc từ MQTT state realtime."""

    def _ac_state(self) -> dict[str, Any]:
        return self.coordinator.get_device_state(self._root_id)

    def _ac_field(self, *keys: str) -> Any:
        """Đọc field từ MQTT state trước, fallback REST value JSON."""
        state = self._ac_state()
        for k in keys:
            v = state.get(k)
            if v is not None:
                return v
        raw = self.coordinator.get_device_raw(self._device_id)
        value = raw.get("value")
        if isinstance(value, str):
            try:
                import json as _json
                parsed = _json.loads(value)
                if isinstance(parsed, dict):
                    for k in keys:
                        v = parsed.get(k)
                        if v is not None:
                            return v
            except (ValueError, TypeError):
                pass
        return None


class HunonicACModeSensor(_HunonicACBase):
    """Chế độ hoạt động điều hòa IR (Cool / Heat / Dry / Fan / Auto)."""

    _attr_icon = "mdi:air-conditioner"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(_IR_MODE_LABELS.values()) + ["Off"]

    @property
    def unique_id(self) -> str:
        return f"hunonic_{self._device_id}_ac_mode"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', self._device_id)} - Chế độ"

    @property
    def native_value(self) -> str:
        action = self._ac_field("action")
        if action is not None:
            try:
                if int(action) == 2:
                    return "Off"
            except (TypeError, ValueError):
                pass
        mode = self._ac_field("mode")
        if mode is not None:
            try:
                return _IR_MODE_LABELS.get(int(mode), str(mode))
            except (TypeError, ValueError):
                pass
        return "unknown"


class HunonicACTempSensor(_HunonicACBase):
    """Nhiệt độ đặt (setpoint) của điều hòa IR (°C)."""

    _attr_icon = "mdi:thermometer"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°C"

    @property
    def unique_id(self) -> str:
        return f"hunonic_{self._device_id}_ac_temp"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', self._device_id)} - Nhiệt độ đặt"

    @property
    def native_value(self) -> float | None:
        t = self._ac_field("temp", "temperature", "set_temp")
        if t is not None:
            try:
                return float(t)
            except (TypeError, ValueError):
                pass
        return None


class HunonicACFanSensor(_HunonicACBase):
    """Tốc độ quạt điều hòa IR (Auto / Min / Low / Medium / High / Max)."""

    _attr_icon = "mdi:fan"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(_IR_FAN_LABELS.values())

    @property
    def unique_id(self) -> str:
        return f"hunonic_{self._device_id}_ac_fan"

    @property
    def name(self) -> str:
        return f"{self._device.get('name', self._device_id)} - Tốc độ quạt"

    @property
    def native_value(self) -> str:
        fan = self._ac_field("fan", "fan_speed", "wind")
        if fan is not None:
            try:
                return _IR_FAN_LABELS.get(int(fan), str(fan))
            except (TypeError, ValueError):
                pass
        return "unknown"


# ── Sensor Cảm biến Nhiệt độ & Độ ẩm (thswifi, ...) ─────────────────────────

class _HunonicTHBase(_HunonicSensorBase):
    """Base sensor cho cảm biến nhiệt độ & độ ẩm (thswifi, ...)."""

    @property
    def available(self) -> bool:
        """Cảm biến môi trường pin ngủ định kỳ luôn available để Home Assistant hiển thị số đo."""
        return True

    def _extract_number(self, *keys: str) -> float | None:
        """Tìm giá trị số từ MQTT state, REST raw, value, data_extra, root_extra, meta."""
        # 1. Kiểm tra MQTT state realtime (theo cả root_id và device_id)
        for rid in (self._root_id, self._device_id):
            if not rid:
                continue
            state = self.coordinator.get_device_state(rid)
            if isinstance(state, dict):
                for k in keys:
                    if k in state and state[k] is not None:
                        val = self._try_parse_float(state[k])
                        if val is not None:
                            return val
                for sub_key in ("data", "params", "extra", "val", "data_extra", "value", "status"):
                    sub = state.get(sub_key)
                    if isinstance(sub, dict):
                        for k in keys:
                            if k in sub and sub[k] is not None:
                                val = self._try_parse_float(sub[k])
                                if val is not None:
                                    return val
                    elif isinstance(sub, str):
                        parsed = self._parse_json_dict(sub)
                        if isinstance(parsed, dict):
                            for k in keys:
                                if k in parsed and parsed[k] is not None:
                                    val = self._try_parse_float(parsed[k])
                                    if val is not None:
                                        return val

        # 2. Kiểm tra device raw từ REST API và self._device
        raw = self.coordinator.get_device_raw(self._device_id) or self._device
        if isinstance(raw, dict):
            for k in keys:
                if k in raw and raw[k] is not None:
                    val = self._try_parse_float(raw[k])
                    if val is not None:
                        return val

            # Kiểm tra trường 'value'
            val_field = raw.get("value")
            parsed_val = self._parse_json_dict(val_field)
            if isinstance(parsed_val, dict):
                for k in keys:
                    if k in parsed_val and parsed_val[k] is not None:
                        val = self._try_parse_float(parsed_val[k])
                        if val is not None:
                            return val

            # Kiểm tra 'data_extra', 'root_extra', 'param', 'params', 'extra'
            for extra_field in ("data_extra", "root_extra", "param", "params", "extra", "status", "DeviceStatus"):
                extra_data = self._parse_json_dict(raw.get(extra_field))
                if isinstance(extra_data, dict):
                    for k in keys:
                        if k in extra_data and extra_data[k] is not None:
                            val = self._try_parse_float(extra_data[k])
                            if val is not None:
                                return val

            # Kiểm tra 'meta' list
            meta_list = raw.get("meta") or self._device.get("meta")
            if isinstance(meta_list, list):
                for m in meta_list:
                    if isinstance(m, dict) and m.get("meta_key") in keys and m.get("value") is not None:
                        val = self._try_parse_float(m.get("value"))
                        if val is not None:
                            return val

        return None

    def _extract_from_delimited_value(self, index: int, is_temp: bool = True) -> float | None:
        """Nếu field value là chuỗi ghép '28.5,65' hoặc '28.5-65', tách theo index hoặc regex."""
        candidates: list[Any] = []
        for rid in (self._root_id, self._device_id):
            if not rid:
                continue
            st = self.coordinator.get_device_state(rid)
            if isinstance(st, dict):
                if st.get("value") is not None:
                    candidates.append(st["value"])
                if st.get("val") is not None:
                    candidates.append(st["val"])

        raw = self.coordinator.get_device_raw(self._device_id) or self._device
        if isinstance(raw, dict):
            if raw.get("value") is not None:
                candidates.append(raw["value"])

        for val_field in candidates:
            if val_field is None:
                continue
            val_str = str(val_field).strip()

            # Regex trích xuất trực tiếp
            if is_temp:
                m = re.search(r'(?:temp|temperature|t|nhiet|nhiệt)[:=\s]*([+-]?\d+(?:\.\d+)?)', val_str, re.I)
                if m:
                    val = self._try_parse_float(m.group(1))
                    if val is not None:
                        return val
            else:
                m = re.search(r'(?:humi|humidity|hum|h|do_am|độ ẩm)[:=\s]*([+-]?\d+(?:\.\d+)?)', val_str, re.I)
                if m:
                    val = self._try_parse_float(m.group(1))
                    if val is not None:
                        return val

            # Tách theo ký tự phân cách
            for sep in (",", "/", ";", "_", "|", "-"):
                if sep in val_str:
                    parts = [p.strip() for p in val_str.split(sep) if p.strip()]
                    if len(parts) > index:
                        val = self._try_parse_float(parts[index])
                        if val is not None:
                            return val
        return None

    @staticmethod
    def _parse_json_dict(field_data: Any) -> dict[str, Any] | None:
        if isinstance(field_data, dict):
            return field_data
        if isinstance(field_data, str):
            try:
                data = json.loads(field_data)
                if isinstance(data, dict):
                    return data
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _try_parse_float(val: Any) -> float | None:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            s = val.strip().replace("°C", "").replace("°F", "").replace("%", "").replace("C", "").strip()
            try:
                return float(s)
            except (ValueError, TypeError):
                pass
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Thêm thông tin thiết bị vào attributes."""
        attrs: dict[str, Any] = {
            "device_type": self._root_type,
            "root_id": self._root_id,
        }
        raw = self.coordinator.get_device_raw(self._device_id) or self._device
        if isinstance(raw, dict):
            for k in ("battery", "bat", "pin", "voltage", "rssi", "fw_version", "version"):
                if k in raw and raw[k] is not None:
                    attrs[k] = raw[k]
        state = self.coordinator.get_device_state(self._root_id)
        if isinstance(state, dict):
            for k in ("battery", "bat", "pin", "rssi", "signal"):
                if k in state and state[k] is not None:
                    attrs[k] = state[k]
        return attrs


class HunonicTemperatureSensor(_HunonicTHBase):
    """Sensor nhiệt độ môi trường (°C) cho thswifi."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = _TEMP_CELSIUS
    _attr_icon = "mdi:thermometer"

    @property
    def unique_id(self) -> str:
        dev_id = self._device_id or self._root_id
        return f"hunonic_sensor_{dev_id}_temperature"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        return f"{device_name} - Nhiệt độ"

    @property
    def native_value(self) -> float | None:
        val = self._extract_number(
            "temp", "temperature", "t", "val_temp", "val_t", "nhiet_do", "nhietdo",
            "aroundTemp", "sensor_temp", "temperature_current", "temp_current",
            "celsius", "te", "nd"
        )
        if val is None:
            val = self._extract_from_delimited_value(index=0, is_temp=True)
        if val is not None:
            # Scale nếu phần cứng gửi 285 thay vì 28.5 hoặc 2850 thay vì 28.5
            if 100 < abs(val) <= 1000:
                val = val / 10.0
            elif abs(val) > 1000:
                val = val / 100.0
            return round(val, 1)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        humi_val = self._extract_number(
            "humi", "humidity", "hum", "h", "val_humi", "val_hum", "val_h",
            "do_am", "doam", "aroundHumidity", "sensor_humi"
        )
        if humi_val is None:
            humi_val = self._extract_from_delimited_value(index=1, is_temp=False)
        if humi_val is not None:
            if 100 < humi_val <= 1000:
                humi_val = humi_val / 10.0
            elif humi_val > 1000:
                humi_val = humi_val / 100.0
            attrs["humidity"] = round(max(0.0, min(100.0, humi_val)), 1)
        return attrs


class HunonicHumiditySensor(_HunonicTHBase):
    """Sensor độ ẩm môi trường (%RH) cho thswifi."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:water-percent"

    @property
    def unique_id(self) -> str:
        dev_id = self._device_id or self._root_id
        return f"hunonic_sensor_{dev_id}_humidity"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        return f"{device_name} - Độ ẩm"

    @property
    def native_value(self) -> float | None:
        val = self._extract_number(
            "humi", "humidity", "hum", "h", "val_humi", "val_hum", "val_h",
            "do_am", "doam", "aroundHumidity", "sensor_humi",
            "humidity_current", "humi_current", "relative_humidity", "rh", "da"
        )
        if val is None:
            val = self._extract_from_delimited_value(index=1, is_temp=False)
        if val is not None:
            # Scale nếu phần cứng gửi 650 thay vì 65 hoặc 6500 thay vì 65
            if 100 < val <= 1000:
                val = val / 10.0
            elif val > 1000:
                val = val / 100.0
            val = max(0.0, min(100.0, val))
            return round(val, 1)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        temp_val = self._extract_number(
            "temp", "temperature", "t", "val_temp", "val_t", "nhiet_do", "nhietdo",
            "aroundTemp", "sensor_temp"
        )
        if temp_val is None:
            temp_val = self._extract_from_delimited_value(index=0, is_temp=True)
        if temp_val is not None:
            if 100 < abs(temp_val) <= 1000:
                temp_val = temp_val / 10.0
            elif abs(temp_val) > 1000:
                temp_val = temp_val / 100.0
            attrs["temperature"] = round(temp_val, 1)
        return attrs


class HunonicBatterySensor(_HunonicTHBase):
    """Sensor mức pin cho cảm biến (%)."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:battery"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        dev_id = self._device_id or self._root_id
        return f"hunonic_sensor_{dev_id}_battery"

    @property
    def name(self) -> str:
        device_name = self._device.get("name", self._device_id)
        return f"{device_name} - Pin"

    @property
    def native_value(self) -> int | None:
        val = self._extract_number("battery", "bat", "pin", "val_pin", "val_bat", "percentage", "power_bat")
        if val is not None:
            if val > 100:
                if 2000 <= val <= 3300:
                    return max(0, min(100, int((val - 2000) / 13)))
            return max(0, min(100, int(val)))
        return None

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Bật mặc định cho cảm biến để người dùng theo dõi pin."""
        return True


