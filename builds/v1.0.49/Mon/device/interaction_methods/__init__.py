from Mon.device.interaction_methods.adb import ADBInteraction
from Mon.device.interaction_methods.base import BaseInteraction
from Mon.device.interaction_methods.browser import BrowserInteraction
from Mon.device.interaction_methods.do_nothing import DoNothingInteraction
from Mon.device.interaction_methods.foreground_post_message import ForegroundPostMessageInteraction
from Mon.device.interaction_methods.genshin import GenshinInteraction, INPUT, MOUSEINPUT, SendInput
from Mon.device.interaction_methods.keys import ADB_KEY_MAP, PYDIRECT_KEY_MAP, normalize_pydirect_key, vk_key_dict
from Mon.device.interaction_methods.post_message import PostMessageInteraction
from Mon.device.interaction_methods.pydirect import PyDirectInteraction
from Mon.device.interaction_methods.pynput import PynputInteraction
from Mon.device.interaction_methods.swipe import insert_swipe
