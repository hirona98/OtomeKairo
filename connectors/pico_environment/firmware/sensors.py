"""SHT41, VEML7700 and SCD4x on Pico I2C0 (100 kHz).

Register commands and conversion factors follow the manufacturers' datasheets;
see this connector's README for references.
"""

import struct
import time

from protocol import CHANNELS


def sensirion_words(data):
    if not data or len(data) % 3:
        raise ValueError("invalid sensor reply length")
    words = []
    for offset in range(0, len(data), 3):
        crc = 0xFF
        for value in data[offset:offset + 2]:
            crc ^= value
            for _ in range(8):
                crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        if crc != data[offset + 2]:
            raise ValueError("sensor CRC mismatch")
        words.append((data[offset] << 8) | data[offset + 1])
    return words


class NotReadyError(Exception):
    pass


class Sensors:
    def __init__(self, i2c):
        self.i2c = i2c
        self.co2_started = False

    def sht41(self):
        # High precision single shot without the heater.
        self.i2c.writeto(0x44, b"\xfd")
        time.sleep(0.01)
        temperature, humidity = sensirion_words(self.i2c.readfrom(0x44, 6))
        # Sensirion specifies limiting converted relative humidity to 0..100%.
        return (-45 + 175 * temperature / 65535,
                min(100, max(0, -6 + 125 * humidity / 65535)))

    def veml7700(self):
        # Gain 1/8, integration 100 ms, power saving disabled.
        self.i2c.writeto_mem(0x10, 0x00, b"\x00\x10")
        self.i2c.writeto_mem(0x10, 0x03, b"\x00\x00")
        time.sleep(0.12)
        raw = struct.unpack("<H", self.i2c.readfrom_mem(0x10, 0x04, 2))[0]
        if raw == 0xFFFF:
            raise ValueError("illuminance sensor saturated")
        lux = raw * 0.5376
        if lux > 1000:
            lux = (((6.0135e-13 * lux - 9.3924e-9) * lux + 8.1488e-5) * lux + 1.0023) * lux
        return (lux,)

    def scd4x(self):
        try:
            if not self.co2_started:
                self.i2c.writeto(0x62, b"\x3f\x86")
                time.sleep(0.5)
                self.i2c.writeto(0x62, b"\x21\xb1")
                self.co2_started = True
                time.sleep(5.1)
            self.i2c.writeto(0x62, b"\xe4\xb8")
            time.sleep(0.002)
            if not (sensirion_words(self.i2c.readfrom(0x62, 3))[0] & 0x07FF):
                raise NotReadyError("CO2 measurement not ready")
            self.i2c.writeto(0x62, b"\xec\x05")
            time.sleep(0.002)
            co2, _, _ = sensirion_words(self.i2c.readfrom(0x62, 9))
            if co2 == 0:
                raise ValueError("invalid CO2 measurement")
            return (co2,)
        except OSError:
            self.co2_started = False
            raise

    def measure(self):
        samples = []
        for channels, read in ((('temperature', 'humidity'), self.sht41),
                               (('illuminance',), self.veml7700),
                               (('co2',), self.scd4x)):
            try:
                values = read()
            except (OSError, NotReadyError, ValueError) as exc:
                code = "invalid_device_value" if isinstance(exc, ValueError) else "acquisition_failed"
                for channel in channels:
                    samples.append({"channel_id": channel, "status": "failed",
                                    "payload": None, "error_code": code})
            else:
                for channel, value in zip(channels, values):
                    samples.append({"channel_id": channel, "status": "succeeded",
                                    "payload": {"kind": "measurement", "value": value,
                                                "unit": CHANNELS[channel][1]}, "error_code": None})
        return samples
