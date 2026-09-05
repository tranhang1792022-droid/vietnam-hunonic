"""Button entity cho chuông cửa RF, quạt IR, điều hòa IR và các remote học lệnh Hunonic.

Hỗ trợ:
- Chuông cửa: "Reo chuông" (kích hoạt chuông rfdb qua hub RF cha hsrf)
- Quạt IR (Quạt T4):
    + "Bật quạt"
    + "Tắt quạt"
    + "Tăng tốc độ"
    + "Quay (Đảo gió)"
    + "Gió tự nhiên"
    + "Chức năng khác"
- Điều hòa IR:
    + Điều hòa T2 (Midea): đúng 8 nút (Bật, Tắt, Tăng nhiệt, Giảm nhiệt, Cool, Fan, Dry, Auto)
    + Điều hòa T4 (Daikin): đúng 10 nút (8 nút cơ bản + Vẫy dọc + Vẫy ngang)
- Tự động quét toàn bộ Remote học lệnh IR / RF khác trong tài khoản:
    + Cửa cuốn backup (4 nút: Mở/Bật, Đóng/Tắt, Khóa/Dừng, Đổi trạng thái)
    + Khiển quạt trần (8 nút: 1..6, Bật, Tắt)
    + TV T1 / TV T3 (các nút nguồn, âm lượng, chuyển kênh, điều hướng, menu...)
    + Đèn ngủ T3 và mọi thiết bị học lệnh mới thêm vào tài khoản.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    IR_AC_TYPES,
    IR_FAN_BTN_NATURAL,
    IR_FAN_BTN_OFF,
    IR_FAN_BTN_ON,
    IR_FAN_BTN_SPEED_UP,
    IR_FAN_BTN_SWING,
    IR_FAN_REMOTE_TYPES,
)
from .coordinator import HunonicCoordinator
from .entity_setup import setup_entities

_LOGGER = logging.getLogger(__name__)

# Ánh xạ tên nút và icon thân thiện cho các nút học lệnh
_BTN_LABEL_MAP: dict[str, str] = {
    "powerOn": "Bật",
    "powerOff": "Tắt",
    "power": "Nguồn (Power)",
    "speed": "Tốc độ",
    "shake": "Quay (Đảo gió)",
    "wind": "Gió tự nhiên",
    "more": "Chức năng khác",
    "vol+": "Âm lượng (+)",
    "vol-": "Âm lượng (-)",
    "ch+": "Kênh (+)",
    "ch-": "Kênh (-)",
    "up": "Lên (Up)",
    "down": "Xuống (Down)",
    "left": "Trái (Left)",
    "right": "Phải (Right)",
    "ok": "OK / Chọn",
    "home": "Trang chủ (Home)",
    "back": "Quay lại (Back)",
    "input": "Đầu vào (Input)",
    "menu": "Menu",
    "mute": "Tắt tiếng (Mute)",
    "alarm": "Khóa / Dừng (Alarm)",
    "setting": "Cài đặt",
    "1": "Số 1",
    "2": "Số 2",
    "3": "Số 3",
    "4": "Số 4",
    "5": "Số 5",
    "6": "Số 6",
    "7": "Số 7",
    "8": "Số 8",
    "9": "Số 9",
    "0": "Số 0",
}

_BTN_ICON_MAP: dict[str, str] = {
    "powerOn": "mdi:power",
    "power": "mdi:power",
    "powerOff": "mdi:power-off",
    "speed": "mdi:speedometer",
    "shake": "mdi:rotate-3d-variant",
    "wind": "mdi:weather-windy",
    "more": "mdi:dots-horizontal",
    "vol+": "mdi:volume-plus",
    "vol-": "mdi:volume-minus",
    "ch+": "mdi:arrow-up-bold",
    "ch-": "mdi:arrow-down-bold",
    "up": "mdi:arrow-up",
    "down": "mdi:arrow-down",
    "left": "mdi:arrow-left",
    "right": "mdi:arrow-right",
    "ok": "mdi:checkbox-blank-circle-outline",
    "home": "mdi:home",
    "back": "mdi:keyboard-backspace",
    "input": "mdi:import",
    "menu": "mdi:menu",
    "mute": "mdi:volume-mute",
    "alarm": "mdi:alarm-bell",
    "setting": "mdi:cog",
}


def _is_doorbell_or_chime(device: dict[str, Any]) -> bool:
    """Kiểm tra xem thiết bị có phải là chuông cửa (rfdb) không."""
    rt = str(device.get("root_type") or "").lower().strip()
    name = str(device.get("name") or "").lower().strip()
    if any(t in rt for t in ("rfdb", "rfbell", "doorbell", "chime", "bell")):
        return True
    if any(n in name for n in ("chuông", "chuong", "doorbell", "chime", "bell")):
        if rt not in ("sswitch2v", "switch", "sswitch1", "sswitch2", "sswitch3", "sswitch4", "hsrf"):
            return True
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Thiết lập button entities tự động quét cho mọi thiết bị IR, RF và Chuông cửa."""

    def _build(coordinator: HunonicCoordinator, device: dict[str, Any]):
        rt = str(device.get("root_type") or "")
        name = str(device.get("name") or "")
        name_up = name.upper()
        meta = device.get("meta") or []
        cate_id = None
        for m in meta:
            if isinstance(m, dict) and m.get("meta_key") == "irchild_cate_id":
                cate_id = str(m.get("value"))

        ents: list[ButtonEntity] = []

        # 1. Chuông cửa RF
        if _is_doorbell_or_chime(device):
            ents.append(HunonicDoorbellButton(coordinator, device))

        # 2. Quạt IR (QUẠT T4 và quạt học lệnh)
        is_fan_ir = (rt in IR_FAN_REMOTE_TYPES) and (
            cate_id == "19" or "QUẠT" in name_up or "FAN" in name_up
        )
        if is_fan_ir:
            rem = device.get("remote")
            btn_codes: dict[str, str] = {}
            if isinstance(rem, dict):
                for k, v in rem.items():
                    if isinstance(v, str) and len(v) > 20:
                        btn_codes[k] = v
            elif isinstance(rem, list):
                for item in rem:
                    if isinstance(item, dict):
                        k = item.get("key_button") or item.get("key_name") or item.get("key")
                        v = item.get("key_value") or item.get("value")
                        if k and isinstance(v, str) and len(v) > 20:
                            btn_codes[k] = v

            fan_specs = [
                ("powerOn", "Bật quạt", "mdi:fan", IR_FAN_BTN_ON, "power_on"),
                ("powerOff", "Tắt quạt", "mdi:fan-off", IR_FAN_BTN_OFF, "power_off"),
                ("speed", "Tăng tốc độ", "mdi:speedometer", IR_FAN_BTN_SPEED_UP, "speed_up"),
                ("shake", "Quay (Đảo gió)", "mdi:rotate-3d-variant", IR_FAN_BTN_SWING, "swing"),
                ("wind", "Gió tự nhiên", "mdi:weather-windy", IR_FAN_BTN_NATURAL, "natural"),
                ("more", "Chức năng khác", "mdi:dots-horizontal", 0, "more"),
            ]
            for btn_key, label, icon, act, suffix in fan_specs:
                code = btn_codes.get(btn_key)
                if code or not btn_codes:
                    ents.append(
                        HunonicIRFanActionButton(
                            coordinator, device, label, icon, act, suffix, key_code=code
                        )
                    )

        # 3. Điều hòa IR (Điều hòa T2, Điều hòa T4...)
        is_ac = (rt in IR_AC_TYPES) and (
            cate_id == "1" or ("ĐIỀU" in name_up or "AC" in name_up or "AIR" in name_up or "T4" in name_up or "T2" in name_up)
        ) and not is_fan_ir

        if is_ac:
            val_obj: dict[str, Any] = {}
            val_str = device.get("value")
            if isinstance(val_str, str):
                try:
                    val_obj = json.loads(val_str)
                except Exception:
                    pass

            brand_id = None
            for m in device.get("meta") or []:
                if isinstance(m, dict) and m.get("meta_key") == "irchild_brand_id" and m.get("value"):
                    try:
                        brand_id = int(m["value"])
                    except Exception:
                        pass
            if not brand_id and isinstance(val_obj, dict) and val_obj.get("brand"):
                brand_id = int(val_obj["brand"])
            if not brand_id:
                brand_id = 1934 if ("MIDEA" in name_up or "T2" in name_up) else 14

            rem = device.get("remote") or []
            has_swing_v = (brand_id == 14) or ("T4" in name_up)
            has_swing_h = (brand_id == 14) or ("T4" in name_up)

            if isinstance(rem, list):
                if any(r.get("key_name") == "swingv" for r in rem):
                    has_swing_v = True
                if any(r.get("key_name") == "swingh" for r in rem):
                    has_swing_h = True

            if isinstance(val_obj, dict):
                if int(val_obj.get("swingv", -1)) >= 0:
                    has_swing_v = True
                elif int(val_obj.get("swingv", -1)) == -1 and brand_id != 14:
                    has_swing_v = False
                if int(val_obj.get("swingh", -1)) >= 0:
                    has_swing_h = True
                elif int(val_obj.get("swingh", -1)) == -1 and brand_id != 14:
                    has_swing_h = False

            # Chuẩn 8 nút cơ bản cho mọi điều hòa (Điều hòa T2 có đủ 8 nút này)
            ents.extend([
                HunonicACCommandButton(coordinator, device, "Bật điều hòa", "mdi:power", "power_on", brand_id, action_type="power_on"),
                HunonicACCommandButton(coordinator, device, "Tắt điều hòa", "mdi:power-off", "power_off", brand_id, action_type="power_off"),
                HunonicACCommandButton(coordinator, device, "Tăng nhiệt độ (+)", "mdi:thermometer-plus", "temp_up", brand_id, action_type="temp_up"),
                HunonicACCommandButton(coordinator, device, "Giảm nhiệt độ (-)", "mdi:thermometer-minus", "temp_down", brand_id, action_type="temp_down"),
                HunonicACCommandButton(coordinator, device, "Làm lạnh (Cool)", "mdi:snowflake", "mode_cool", brand_id, action_type="mode_cool"),
                HunonicACCommandButton(coordinator, device, "Quạt gió (Fan)", "mdi:fan", "mode_fan", brand_id, action_type="mode_fan"),
                HunonicACCommandButton(coordinator, device, "Hút ẩm (Dry)", "mdi:water-percent", "mode_dry", brand_id, action_type="mode_dry"),
                HunonicACCommandButton(coordinator, device, "Tự động (Auto)", "mdi:autorenew", "mode_auto", brand_id, action_type="mode_auto"),
            ])

            # Các nút cánh vẫy độc lập (Điều hòa T4 có thêm 2 nút này -> đúng 10 nút)
            if has_swing_v:
                ents.append(
                    HunonicACSwingButton(
                        coordinator, device, "Vẫy dọc", "mdi:arrow-up-down", "swingv", "swing_v", brand_id
                    )
                )
            if has_swing_h:
                ents.append(
                    HunonicACSwingButton(
                        coordinator, device, "Vẫy ngang", "mdi:arrow-left-right", "swingh", "swing_h", brand_id
                    )
                )

        # 4. Tự động quét mọi Remote học lệnh khác (Cửa cuốn backup, Khiển quạt trần, Tivi, Đèn ngủ...)
        rem_all = device.get("remote")
        if rem_all and isinstance(rem_all, list) and not is_ac and not is_fan_ir:
            for idx, r in enumerate(rem_all):
                if not isinstance(r, dict):
                    continue
                kbtn = str(r.get("key_button") or "").strip()
                kname = str(r.get("key_name") or "").strip()
                kval = r.get("key_value")
                btn_key = kbtn or kname
                # Bỏ qua các tham số cấu hình điều hòa nếu có
                if btn_key.lower() in ("temp_min", "temp_max", "mode", "fan", "swingv", "swingh"):
                    continue
                if kval and isinstance(kval, str) and len(kval) > 15:
                    label = kname if kname else _BTN_LABEL_MAP.get(btn_key, btn_key)
                    icon = _BTN_ICON_MAP.get(btn_key, "mdi:remote")
                    ents.append(
                        HunonicCustomRemoteButton(
                            coordinator=coordinator,
                            device=device,
                            btn_key=btn_key,
                            label=label,
                            icon=icon,
                            key_code=kval,
                            idx=idx,
                        )
                    )

        return ents

    setup_entities(hass, entry, async_add_entities, _build)


class HunonicDoorbellButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút bấm 'Reo chuông' cho thiết bị chuông cửa RF Hunonic (hsrf / rfdb)."""

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
        return f"{self._device.get('name', 'Chuông cửa')} - Reo chuông"

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
    def _uid(self) -> int:
        try:
            return int(self.coordinator._user_id or 0)
        except (TypeError, ValueError):
            return 0

    async def async_press(self) -> None:
        """Bấm nút -> kích hoạt chuông cửa rfdb / hsrf reo lên."""
        payload = {
            "hsrf": 440,
            "turn": 1,
            "u": self._uid,
        }
        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.info("Đã kích hoạt reo chuông cửa cho %s: %s", self._device.get("name"), payload)


class HunonicIRFanActionButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút bấm điều khiển nhanh cho Quạt học lệnh IR (Bật, Tắt, Tăng tốc, Quay, Gió tự nhiên)."""

    def __init__(
        self,
        coordinator: HunonicCoordinator,
        device: dict[str, Any],
        btn_label: str,
        icon: str,
        action: int,
        suffix: str,
        key_code: str | None = None,
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
        self._key_code = key_code

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
        """Gửi lệnh hồng ngoại khi nhấn nút."""
        if self._key_code:
            payload: dict[str, Any] = {
                "irwifiv2": 1,
                "type": 2,
                "data": self._key_code,
                "u": int(self.coordinator._user_id or 0),
            }
        else:
            payload = {
                "u": int(self.coordinator._user_id or 0),
                "irwifiv2": 1,
                self._root_type: 0,
                "act_id": 0,
                "action": self._action,
                "src": 1,
            }

        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("Đã gửi nút quạt %s tới %s", self._btn_label, self._device.get("name"))


class HunonicCustomRemoteButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút bấm học lệnh IR / RF tùy biến quét tự động từ tài khoản."""

    def __init__(
        self,
        coordinator: HunonicCoordinator,
        device: dict[str, Any],
        btn_key: str,
        label: str,
        icon: str,
        key_code: str,
        idx: int = 0,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))
        self._btn_key = btn_key
        self._label = label
        self._attr_icon = icon
        self._key_code = key_code
        self._idx = idx

    @property
    def unique_id(self) -> str:
        return f"hunonic_btn_{self._device_id}_{self._btn_key}_{self._idx}"

    @property
    def name(self) -> str:
        dev_name = self._device.get("name") or f"Remote {self._device_id}"
        return f"{dev_name} - {self._label}"

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
    def _uid(self) -> int:
        try:
            return int(self.coordinator._user_id or 0)
        except (TypeError, ValueError):
            return 0

    async def async_press(self) -> None:
        """Bấm nút -> gửi mã xung qua Hub cha tương ứng."""
        # 1. RF child: gửi {"hsrf": 471, "data": key_code, "u": uid}
        if self._root_type in ("rfchild", "rfdb") or "hsrf" in str(self._device.get("topicsub", "")).lower():
            payload = {
                "hsrf": 471,
                "data": self._key_code,
                "u": self._uid,
            }
        else:
            # 2. IR child: gửi {"irwifiv2": 1, "type": 2, "data": key_code, "u": uid}
            payload = {
                "irwifiv2": 1,
                "type": 2,
                "data": self._key_code,
                "u": self._uid,
            }

        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("Gửi nút học lệnh %s (%s): %s", self.name, self._btn_key, payload)


class HunonicACSwingButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút bấm điều khiển cánh vẫy gió cho điều hòa (Vẫy dọc / Vẫy ngang)."""

    def __init__(
        self,
        coordinator: HunonicCoordinator,
        device: dict[str, Any],
        btn_label: str,
        icon: str,
        swing_field: str,  # "swingv" hoặc "swingh"
        suffix: str,
        brand_id: int = 14,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))
        self._btn_label = btn_label
        self._attr_icon = icon
        self._swing_field = swing_field
        self._suffix = suffix
        self._brand_id = brand_id
        self._is_swinging = False

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
        """Bật/tắt cánh vẫy gió gửi gói tin AC chuẩn Hunonic."""
        self._is_swinging = not self._is_swinging
        code = 15 if self._is_swinging else 0

        val_obj: dict[str, Any] = {}
        val_str = self._device.get("value")
        if isinstance(val_str, str):
            try:
                val_obj = json.loads(val_str)
            except Exception:
                pass

        temp = int(val_obj.get("temp") or 26)
        mode = int(val_obj.get("mode") or (3 if self._brand_id == 14 else 0))
        fan = int(val_obj.get("fan") or (10 if self._brand_id == 14 else 0))

        payload: dict[str, Any] = {
            "irwifiv2": 1,
            "type": 1,
            "brand": self._brand_id,
            "power": 1,
            "temp": temp,
            "mode": mode,
            "fan": fan,
            "act": 0,
            "u": int(self.coordinator._user_id or 0),
            self._swing_field: code,
        }

        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("Đã gửi lệnh cánh vẫy %s (%s=%s) tới %s", self._btn_label, self._swing_field, code, self._device.get("name"))


class HunonicACCommandButton(CoordinatorEntity[HunonicCoordinator], ButtonEntity):
    """Nút bấm chức năng điều khiển điều hòa (Bật, Tắt, Tăng nhiệt độ, Giảm nhiệt độ, Đổi chế độ)."""

    def __init__(
        self,
        coordinator: HunonicCoordinator,
        device: dict[str, Any],
        btn_label: str,
        icon: str,
        suffix: str,
        brand_id: int,
        action_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id: str = str(device.get("id", ""))
        self._root_id: str = str(device.get("root_id", ""))
        self._root_type: str = str(device.get("root_type", ""))
        self._btn_label = btn_label
        self._attr_icon = icon
        self._suffix = suffix
        self._brand_id = brand_id
        self._action_type = action_type

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
        """Gửi lệnh điều hòa tương ứng với nút bấm."""
        val_obj: dict[str, Any] = {}
        val_str = self._device.get("value")
        if isinstance(val_str, str):
            try:
                val_obj = json.loads(val_str)
            except Exception:
                pass

        temp = int(val_obj.get("temp") or 26)
        power = int(val_obj.get("power", 1))
        is_daikin = (self._brand_id == 14)
        mode = int(val_obj.get("mode") or (3 if is_daikin else 0))
        fan = int(val_obj.get("fan") or (10 if is_daikin else 0))

        if self._action_type == "power_on":
            power = 1
        elif self._action_type == "power_off":
            power = 0
        elif self._action_type == "temp_up":
            temp = min(30, temp + 1)
            power = 1
        elif self._action_type == "temp_down":
            temp = max(16, temp - 1)
            power = 1
        elif self._action_type == "mode_cool":
            mode = 3 if is_daikin else 0
            power = 1
        elif self._action_type == "mode_fan":
            mode = 6 if is_daikin else 4
            power = 1
        elif self._action_type == "mode_dry":
            mode = 2
            power = 1
        elif self._action_type == "mode_auto":
            mode = 0 if is_daikin else 2
            power = 1

        payload: dict[str, Any] = {
            "irwifiv2": 1,
            "type": 1,
            "brand": self._brand_id,
            "power": power,
            "temp": temp,
            "mode": mode,
            "fan": fan,
            "act": 0,
            "u": int(self.coordinator._user_id or 0),
        }

        # Duy trì cánh vẫy nếu có
        if int(val_obj.get("swingv", -1)) >= 0:
            payload["swingv"] = int(val_obj["swingv"])
        if int(val_obj.get("swingh", -1)) >= 0:
            payload["swingh"] = int(val_obj["swingh"])

        dev_id = self._device.get("id")
        if dev_id:
            try:
                payload["child_id"] = int(dev_id)
            except (ValueError, TypeError):
                payload["child_id"] = dev_id

        # Cập nhật state nội bộ
        val_obj["power"] = power
        val_obj["temp"] = temp
        val_obj["mode"] = mode
        self._device["value"] = json.dumps(val_obj)

        await self.coordinator.async_control_device(self._device, payload)
        _LOGGER.debug("Đã gửi lệnh nút điều hòa %s tới %s: %s", self._btn_label, self._device.get("name"), payload)
