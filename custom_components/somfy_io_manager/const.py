"""Constants for the Somfy IO shutter manager."""

from homeassistant.const import Platform

DOMAIN = "somfy_io_manager"
PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.SENSOR]
MANAGER_API_VERSION = 1

CONF_ESPHOME_ENTRY_ID = "esphome_entry_id"
CONF_DEVICE_NAME = "device_name"
CONF_STATUS_ENTITY_ID = "status_entity_id"
CONF_BACKUP_ENTITY_ID = "backup_entity_id"
CONF_SHUTTERS = "shutters"
CONF_SHUTTER_ID = "shutter_id"
CONF_SLOT = "slot"
CONF_TARGET_SLOT = "target_slot"
CONF_NAME = "name"
CONF_AREA_ID = "area_id"
CONF_OPEN_SECONDS = "open_seconds"
CONF_CLOSE_SECONDS = "close_seconds"
CONF_MY_PERCENT = "my_percent"
CONF_STATE = "state"

STATE_ACTIVE = "active"
STATE_UNCERTAIN = "uncertain"

DATA_RUNTIME = "runtime"
STORAGE_VERSION = 1
MAX_SHUTTER_SLOTS = 20
