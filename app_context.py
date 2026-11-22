"""Общие сервисы приложения: создаются один раз в main.py и передаются страницам."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.database import Database, StateStore
    from island.game_detector import GameDetector
    from island.island_window import IslandWindow
    from island.music_controller import MusicController
    from island.notifications import NotificationHub, VolumeWatcher
    from launcher.boost import BoostManager
    from launcher.library_manager import LibraryManager
    from settings import AppSettings
    from system.power import PowerManager
    from system.sampler import Sampler
    from system.tweaks import TweakManager
    from ui.icon_cache import IconCache


@dataclass
class AppContext:
    db: Database
    settings: AppSettings
    sampler: Sampler
    music: MusicController
    island: IslandWindow
    hub: NotificationHub
    detector: GameDetector
    volume: VolumeWatcher
    state: StateStore
    power: PowerManager
    tweaks: TweakManager
    boost: BoostManager
    library: LibraryManager
    icons: IconCache
