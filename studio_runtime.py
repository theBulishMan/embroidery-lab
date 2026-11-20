"""Installation paths; production data and provider credentials stay outside releases."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).parent
APP = Path(os.environ.get('EMBROIDERY_ENGINE_HOME',
    str(Path.home() / 'Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/inkstitch.app')
    if sys.platform == 'darwin' else '/opt/inkstitch'))
RESOURCES = APP / 'Contents/Resources' if sys.platform == 'darwin' else APP
ENGINE = Path(os.environ.get('EMBROIDERY_ENGINE',
    str(APP / 'Contents/MacOS/inkstitch' if sys.platform == 'darwin' else APP / 'inkstitch')))
PALETTE = Path(os.environ.get('EMBROIDERY_THREAD_PALETTE', str(RESOURCES / 'palettes/InkStitch Madeira Rayon.gpl')))
DATA = Path(os.environ.get('EMBROIDERY_DATA_DIR', str(ROOT / 'studio-data')))
CONFIG = Path(os.environ.get('EMBROIDERY_CONFIG_FILE', str(ROOT / '.env.local')))
