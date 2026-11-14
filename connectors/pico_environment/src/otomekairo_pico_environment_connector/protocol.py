"""USB wire constants shared by CPython and the Pico's MicroPython."""

PROTOCOL = "otomekairo.pico-environment.v1"
CHANNELS = {
    "temperature": ("室温（SHT41）", "degC"),
    "humidity": ("相対湿度（SHT41）", "%"),
    "illuminance": ("照度（VEML7700）", "lux"),
    "co2": ("CO₂濃度（SCD4x）", "ppm"),
}
