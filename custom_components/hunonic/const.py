"""Hằng số cho integration Hunonic."""

DOMAIN = "hunonic"

# Web API (login phone+password, lấy device list — topic MÃ HÓA)
WEB_API_URL = "https://web.hunonic.com/api"
BASE_URL = WEB_API_URL + "/api/"  # double /api/

# Mobile API (api.hunonicpro.com) — device list trả topic PLAINTEXT + key/iv.
# listDeviceOfHomeSelect cần token_id từ mobile login (login cần signature
# hunonicEncodeSign — xem docs/reverse-engineering.md §7, CHƯA replicate xong).
MOBILE_API_URL = "https://api.hunonicpro.com/v3/"

# Discovery broker MQTT theo từng thiết bị (mỗi nhà broker khác nhau).
# getInfoMqtt trả danh sách broker (mã hóa AES key=derive_key(root_id), iv=enc[12:28]).
MQTT_INFO_URL = "http://infoserver.hunonicpro.com/HardwareAPI/getInfoMqtt.php"

# MQTT broker TĨNH — chỉ dùng làm fallback nếu getInfoMqtt lỗi.
MQTT_BROKER = "103.109.43.24"      # dự phòng: 123.30.48.196
MQTT_WS_PORT = 8080
MQTT_WS_PATH = "/ws"               # MQTT-over-WebSocket, subprotocol "mqtt"
MQTT_USERNAME = "bestbug"
MQTT_PASSWORD = "bigbugdmm"

# Action điều khiển (đã verify): payload = {"u":uid, "<root_type>":channel0based,
#   "act_id":0, "action":ACTION_ON|ACTION_OFF}, mã hóa AES-CBC key/iv của device,
#   publish tới topicsub (plaintext). State báo về topicpub (=topicsub+"/ok").
ACTION_ON = 1
ACTION_OFF = 2

CONF_PHONE = "phone"
CONF_PASSWORD = "password"
CONF_HOME_ID = "home_id"
CONF_HOME_NAME = "home_name"
CONF_TOKEN_ID = "token_id"
CONF_USER_ID = "user_id"
CONF_HOME_IDS = "home_ids"  # danh sách nhà được chọn (rỗng/không có = tất cả)

PLATFORMS = ["switch", "cover", "fan", "light", "sensor", "select", "climate", "button"]

# Thiết bị điều hòa IR (MQTT action điều khiển qua tín hiệu hồng ngoại).
# irchildv2 = IR child device v2 (điều hòa Hunonic), irremote = remote IR tổng quát.
IR_AC_TYPES = ["irchildv2", "irremote"]

# Mode codes cho điều hòa IR (field "mode" trong MQTT payload)
IR_MODE_AUTO = 0
IR_MODE_COOL = 1
IR_MODE_DRY  = 2
IR_MODE_FAN  = 3
IR_MODE_HEAT = 4

# Fan speed codes (field "fan" trong MQTT payload)
IR_FAN_AUTO   = 0
IR_FAN_MIN    = 1
IR_FAN_LOW    = 2
IR_FAN_MEDIUM = 3
IR_FAN_HIGH   = 4
IR_FAN_MAX    = 5

# Nhiệt độ đặt mặc định và giới hạn (°C)
IR_TEMP_MIN = 16
IR_TEMP_MAX = 30
IR_TEMP_DEFAULT = 25

# ── Quạt học lệnh IR (irchildv2 dùng làm remote quạt) ────────────────────────
# Thiết bị irchildv2 có thể học lệnh IR cho ĐIỀU HÒA hoặc QUẠT.
# Cả hai loại entity (climate + fan) đều được tạo — user tắt loại không dùng.
IR_FAN_REMOTE_TYPES = ["irchildv2", "irremote"]

# Action codes tương ứng với từng nút trên remote quạt học lệnh (xem ảnh app):
# Hàng 1: [Bật 🟢] [Timer ⏰] [Tắt 🔴]
# Hàng 2: [Tăng tốc ↑] [Quay ↻] [Gió tự nhiên ~]
# Hàng 3-5: [1] [2] [3] [4] [5] [6] [7] [8]
# Hàng 6: [Timer ⏰]
IR_FAN_BTN_ON        = 1   # Bật (nút xanh)
IR_FAN_BTN_TIMER1    = 2   # Timer (hàng 1)
IR_FAN_BTN_OFF       = 3   # Tắt (nút đỏ)
IR_FAN_BTN_SPEED_UP  = 4   # Tăng tốc độ
IR_FAN_BTN_SWING     = 5   # Quay/Oscillation (swing)
IR_FAN_BTN_NATURAL   = 6   # Gió tự nhiên / giảm tốc
IR_FAN_BTN_SPD1      = 7   # Tốc độ 1
IR_FAN_BTN_SPD2      = 8   # Tốc độ 2
IR_FAN_BTN_SPD3      = 9   # Tốc độ 3
IR_FAN_BTN_SPD4      = 10  # Tốc độ 4
IR_FAN_BTN_SPD5      = 11  # Tốc độ 5
IR_FAN_BTN_SPD6      = 12  # Tốc độ 6
IR_FAN_BTN_SPD7      = 13  # Tốc độ 7
IR_FAN_BTN_SPD8      = 14  # Tốc độ 8
IR_FAN_BTN_TIMER2    = 15  # Timer (hàng dưới)

SCAN_INTERVAL = 30  # giây
MQTT_RECONNECT_DELAY = 5

SWITCH_TYPES = [
    "wswitch", "wswitch2v", "wswitch3v", "wsdatic", "wsdatic3v", "wswc",
    "lhswitch", "lhswitch2v", "lhswitch3v", "lhrtcsw", "nswitch",
    "swsim", "swsimv2", "swsimv3", "swmini", "swminiv2",
    "swinput", "swinputv2", "sswitch", "sswitch2v", "swstair",
    "daticbs", "daticbsv2", "swshock", "swshockv2", "swshock_hun", "swshohuv2", "wsm",
    "sk02wifi",  # SK02 WiFi switch — 1 kênh, điều khiển như sswitch2v
    "elmeter",  # công tơ điện có điều khiển — đóng/cắt như công tắc
    # Aptomat/công tơ TỔNG wifi — đóng/cắt CẢ NHÀ. Có on/off như công tắc 1 kênh.
    # ⚠️ TẮT là mất điện toàn nhà — không đưa vào automation vô ý.
    "atmwifi", "atmwifiv2",
]
# Công tơ điện (aptomat đo điện): ngoài on/off còn có sensor điện năng/tiền điện
# + công suất tức thời (data_extra.power_current). atmwifi* = công tơ tổng cả nhà.
METER_TYPES = ["elmeter", "atmwifi", "atmwifiv2"]
GATE_HUB_TYPES = ["gatehun", "gatehuwf"]
GATE_TYPES = ["gate", "gatev2", "wsgate"]
DOOR_TYPES = [
    "sdoor2", "sdoor3", "sdoor4", "sdoor5", "sdoor6",
    "sdoor7", "sdoor8", "sdoor9", "sdoor10", "sdoor12",
]
FAN_TYPES = ["fanwifi", "fanac", "fandc", "fanacir"]
LED_TYPES = ["swled", "swledv2", "dled", "duhalled", "radav1", "duhal"]

# RF Chuông cửa & Loa chuông báo (doorbell / chime):
# rfdb = nút bấm chuông cửa RF
# hsrf = bộ chuông / loa chuông Hunonic Smart RF cắm điện trong nhà
CHIME_TYPES = ["hsrf", "hsrfv2"]
DOORBELL_TYPES = ["rfdb", "rfdbv2", "rfbell", "hsrf", "hsrfv2"]

# Cảm biến nhiệt độ & độ ẩm (thswifi, ...)
TH_TYPES = [
    "thswifi", "thswifiv2", "thwifi", "thsensor", "sensortemp", "thwswifi", "swth", "th",
]

def channel_of(index_in_root: int) -> int:
    """index_in_root (1-based) -> chỉ số kênh 0-based dùng trong payload."""
    return max(0, int(index_in_root) - 1)
