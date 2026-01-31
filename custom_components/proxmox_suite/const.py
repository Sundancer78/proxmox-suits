DOMAIN = "proxmox_suite"

CONF_BACKEND = "backend"        # "pve" | "pbs"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN_ID = "token_id"
CONF_TOKEN_SECRET = "token_secret"
CONF_VERIFY_SSL = "verify_ssl"
CONF_NODE = "node"              # required for pve; for pbs usually "localhost"

BACKEND_PVE = "pve"
BACKEND_PBS = "pbs"

DEFAULT_PVE_PORT = 8006
DEFAULT_PBS_PORT = 8007

DEFAULT_VERIFY_SSL = False
UPDATE_INTERVAL_SECONDS = 30

PLATFORMS = ["sensor"]