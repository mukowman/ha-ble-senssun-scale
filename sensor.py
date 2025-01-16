import logging
import asyncio
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfMass
from homeassistant.core import callback
from bleak import BleakClient
from bleak.exc import BleakError
from struct import unpack

_LOGGER = logging.getLogger(__name__)

#WRITE_CHAR = "0000ffb1-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Senssun Body Scale sensor from a config entry."""
    address = config_entry.data["address"]
    _LOGGER.debug(f"Setting up Senssun Body Scale sensor with address: {address}")
    scale = SenssunScaleSensor(hass, address)
    async_add_entities([scale], True)

class SenssunScaleSensor(SensorEntity):
    def __init__(self, hass, address):
        self.hass = hass
        self._address = address
        self._state = None
        self._available = False
        self._attr_name = "Senssun Body Scale Weight"
        self._attr_unique_id = f"ble_scale_{self._address}"
        self._client = None
        self._disconnect_timer = None
        self._connect_lock = asyncio.Lock()
        self._connection_retry_interval = 60  # Retry connection every 60 seconds
        self._retry_task = None
        _LOGGER.debug(f"SenssunScaleSensor initialized with address: {address}")

    @property
    def name(self):
        return self._attr_name

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return UnitOfMass.GRAMS

    @property
    def available(self):
        return self._available

    def read_stable(ctr1):
        """Parse Stable."""
        return int((ctr1 & 0xA0) == 0xA0)
    
    def decode_weight(self, data):
        """Decode weight data for Senssun Scales and return only if stable."""
        _LOGGER.debug(f"Decoding weight data: {data.hex()}")
    
        # Extract relevant data from bytes
        xvalue = data[9:15]  # Adjusted range for required bytes
        (_, weight_raw, _, ctr1) = unpack(">bhhb", xvalue)
    
        # Determine if the measurement is stable
        if not read_stable(ctr1):
            _LOGGER.debug("Measurement is unstable. Weight not returned.")
            return None  # Return None if the measurement is not stable
    
        # Convert the raw weight to grams (assuming 100-gram increments)
        weight_grams = weight_raw * 100
        _LOGGER.debug(f"Stable weight measurement: {weight_grams} grams")
    
        return weight_grams


    def notification_handler(self, sender, data):
        _LOGGER.debug(f"Received notification: {data.hex()}")
        weight = self.decode_weight(data)
        self._state = round(weight, 1)
        self._available = True
        _LOGGER.info(f"Updated weight: {self._state} grams")
        self.async_write_ha_state()
        
        # Reset the disconnect timer
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
        self._disconnect_timer = self.hass.loop.call_later(60, self.disconnect)

    async def connect(self):
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                return

            try:
                self._client = BleakClient(self._address, timeout=10.0)
                await asyncio.wait_for(self._client.connect(), timeout=20.0)
                _LOGGER.debug(f"Connected to BLE Scale: {self._address}")
                self._available = True
                
                await self._client.start_notify(NOTIFY_CHAR, self.notification_handler)
                _LOGGER.debug(f"Notifications started for characteristic: {NOTIFY_CHAR}")
                
                #await self._client.write_gatt_char(WRITE_CHAR, b'\x01', response=False)
                #_LOGGER.debug(f"Wrote to characteristic: {WRITE_CHAR}")
                
                # Set initial disconnect timer
                self._disconnect_timer = self.hass.loop.call_later(60, self.disconnect)
            
            except asyncio.TimeoutError:
                _LOGGER.error(f"Timeout connecting to Senssun Body Scale: {self._address}")
                self._available = False
                self._schedule_retry()
            except BleakError as e:
                _LOGGER.error(f"Error connecting to Senssun Body Scale: {e}")
                self._available = False
                self._schedule_retry()
            except Exception as e:
                _LOGGER.error(f"Unexpected error connecting to Senssun Body Scale: {e}")
                self._available = False
                self._schedule_retry()

    @callback
    def _schedule_retry(self):
        if self._retry_task:
            self._retry_task.cancel()
        self._retry_task = self.hass.async_create_task(self._retry_connect())

    async def _retry_connect(self):
        await asyncio.sleep(self._connection_retry_interval)
        await self.async_update()

    def disconnect(self):
        if self._client and self._client.is_connected:
            asyncio.create_task(self._disconnect())

    async def _disconnect(self):
        try:
            await self._client.disconnect()
            _LOGGER.debug(f"Disconnected from Senssun Body Scale: {self._address}")
        except Exception as e:
            _LOGGER.error(f"Error disconnecting from Senssun Body Scale: {e}")
        finally:
            self._client = None
            self._available = False
            self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """Run when entity will be removed from hass."""
        if self._retry_task:
            self._retry_task.cancel()
        await self._disconnect()

    async def async_update(self):
        """Update the sensor."""
        if not self._client or not self._client.is_connected:
            await self.connect()
