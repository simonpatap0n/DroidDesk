#!/usr/bin/env python3
"""GNOME control panel for Android phones using ADB and scrcpy."""

import base64
import hashlib
import json
import os
import posixpath
import re
import secrets
import shlex
import signal
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import gi
from droidlink_bridge import PORT, DroidLinkBridge

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402


APP_ID = "io.github.example.DroidDesk"
APP_CSS = """
.droiddesk-window .app-sidebar {
  background-color: @view_bg_color;
  border-right: 1px solid @borders;
}
.droiddesk-window .navigation-list {
  background: transparent;
}
.droiddesk-window .navigation-list row {
  min-height: 42px;
  margin: 2px 5px;
  border-radius: 11px;
}
.droiddesk-window .navigation-list row:selected {
  background-color: @accent_bg_color;
}
.droiddesk-window .navigation-list row:selected label,
.droiddesk-window .navigation-list row:selected image {
  color: @accent_fg_color;
}
.droiddesk-window .navigation-section {
  margin: 13px 10px 3px;
  color: @dim_label_fg_color;
  font-size: 0.78em;
  font-weight: 700;
}
.droiddesk-window .navigation-brand {
  padding: 10px 7px 16px;
  border-bottom: 1px solid @borders;
}
.droiddesk-window .brand-icon,
.droiddesk-window .feature-icon {
  color: @accent_color;
}
.droiddesk-window .page-heading {
  margin-bottom: 4px;
}
.droiddesk-window .page-title {
  font-size: 1.85em;
  font-weight: 750;
  letter-spacing: -0.025em;
}
.droiddesk-window .page-description {
  color: @dim_label_fg_color;
  font-size: 1.02em;
}
.droiddesk-window .app-tile {
  min-height: 224px;
  padding: 8px;
  border: 1px solid @borders;
  border-radius: 18px;
  background-color: @card_bg_color;
  transition: background-color 150ms ease, border-color 150ms ease;
}
.droiddesk-window .app-tile:hover {
  border-color: @accent_bg_color;
  background-color: @view_bg_color;
}
.droiddesk-window .app-icon-frame {
  min-width: 152px;
  min-height: 152px;
  border-radius: 28px;
  background-color: alpha(@accent_bg_color, 0.08);
}
.droiddesk-window .app-tile-name {
  font-size: 1.08em;
  font-weight: 650;
}
.droiddesk-window .gallery-thumbnail {
  border-radius: 12px;
  background-color: @view_bg_color;
}
.droiddesk-window .embedded-screen {
  padding: 12px;
  border: 1px solid @borders;
  border-radius: 18px;
  background-color: @card_bg_color;
}
.droiddesk-window .embedded-screen-picture {
  border-radius: 14px;
  background-color: #111116;
}
.droiddesk-window .sms-bubble-incoming,
.droiddesk-window .sms-bubble-outgoing {
  padding: 12px 14px;
  border-radius: 18px;
  max-width: 460px;
}
.droiddesk-window .sms-bubble-incoming {
  background-color: @card_bg_color;
}
.droiddesk-window .sms-bubble-outgoing {
  background-color: alpha(@accent_bg_color, 0.24);
}
.droiddesk-window .sms-status-card {
  padding: 14px;
  border-radius: 14px;
  background-color: @card_bg_color;
}
.droiddesk-window .sms-composer {
  padding: 8px 10px;
  border: 1px solid @borders;
  border-radius: 16px;
  background-color: @card_bg_color;
}
.droiddesk-window .sms-composer-input {
  border-radius: 12px;
  background-color: @view_bg_color;
}
.droiddesk-window .app-status {
  padding: 9px 16px;
  background-color: @view_bg_color;
}
.droiddesk-window .list-panel {
  border: 1px solid @borders;
  border-radius: 16px;
  background-color: @card_bg_color;
}
.droiddesk-window .contact-avatar {
  min-width: 36px;
  min-height: 36px;
  border-radius: 18px;
  background-color: alpha(@accent_bg_color, 0.22);
  color: @accent_color;
  font-weight: 700;
}
.droiddesk-window .list-footer {
  padding: 8px;
  color: @dim_label_fg_color;
}
.droiddesk-window .call-log-row {
  min-height: 62px;
}
.droiddesk-window .sms-sidebar {
  padding: 10px;
  border: 1px solid @borders;
  border-radius: 16px;
  background-color: @card_bg_color;
}
"""
ADB_CANDIDATES = (
    Path.home() / ".local/bin/adb",
    Path.home() / ".local/share/android-sdk/platform-tools/adb",
    Path.home() / "Android/Sdk/platform-tools/adb",
    Path("/usr/bin/adb"),
    Path("/usr/local/bin/adb"),
)
SCRCPY_CANDIDATES = (Path("/usr/bin/scrcpy"), Path("/usr/local/bin/scrcpy"))
SCRCPY_SERVER_CANDIDATES = (
    Path("/usr/share/scrcpy/scrcpy-server"),
    Path("/usr/local/share/scrcpy/scrcpy-server"),
    Path("/usr/lib/scrcpy/scrcpy-server"),
)
MADBFS_CANDIDATES = (
    Path.home() / ".local/bin/madbfs",
    Path("/usr/local/bin/madbfs"),
    Path("/usr/bin/madbfs"),
)
BLUEZ = "org.bluez"
HFP_AUDIO_GATEWAY_UUID = "0000111f-0000-1000-8000-00805f9b34fb"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"
BLUEZ_AGENT_PATH = "/" + APP_ID.replace(".", "/") + "/Agent"
AGENT_XML = """
<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Cancel"/>
</interface></node>
"""


def find_executable(name, candidates):
    found = shutil.which(name)
    if found:
        return found
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def deep_unpack(value):
    if isinstance(value, GLib.Variant):
        return deep_unpack(value.unpack())
    if isinstance(value, dict):
        return {key: deep_unpack(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(deep_unpack(item) for item in value)
    return value


def controller_identity(path, properties):
    controller = path.rsplit("/", 1)[-1]
    try:
        device_path = (Path("/sys/class/bluetooth") / controller / "device").resolve(strict=True)
    except (OSError, RuntimeError):
        device_path = None
    if device_path:
        parts = device_path.parts
        for index, item in enumerate(parts):
            bus = re.fullmatch(r"usb(\d+)", item)
            if not bus:
                continue
            bus_number = bus.group(1)
            ports = [p for p in parts[index + 1 :] if re.fullmatch(rf"{bus_number}-[\d.]+", p)]
            usb_device = ports[-1] if ports else None
            port = usb_device.split("-", 1)[1] if usb_device else None
            description = f"Intégré · USB{bus_number}" if usb_device and _usb_is_fixed(usb_device) else f"USB{bus_number}"
            if port:
                description += f" · port {port}"
            product = _read_text(Path("/sys/bus/usb/devices") / (usb_device or "") / "product")
            if product:
                description += f" · {product}"
            return f"{controller} · {description}"
        if any(re.fullmatch(r"[\da-fA-F]{4}:[\da-fA-F]{2}:[\da-fA-F]{2}\.[\da-fA-F]", p) for p in parts):
            return f"{controller} · PCI"
        if any(p in ("platform", "acpi") for p in parts):
            return f"{controller} · Intégré"
    modalias = str(properties.get("Modalias", "")).lower()
    bus_name = "USB" if modalias.startswith("usb:") else "PCI" if modalias.startswith("pci:") else "emplacement inconnu"
    return f"{controller} · {bus_name}"


def _read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _usb_is_fixed(device):
    return _read_text(Path("/sys/bus/usb/devices") / device / "removable") == "fixed"


def _content_rows(output):
    rows = []
    for line in output.splitlines():
        match = re.match(r"Row:\s*\d+\s+(.*)", line)
        if not match:
            continue
        text = match.group(1)
        tail_match = re.search(r", (body|text|snippet)=", text)
        prefix = text[:tail_match.start()] if tail_match else text
        fields = list(re.finditer(r"(?:^|, )([A-Za-z0-9_]+)=", prefix))
        item = {}
        for index, field in enumerate(fields):
            start = field.end()
            end = fields[index + 1].start() if index + 1 < len(fields) else len(prefix)
            item[field.group(1)] = prefix[start:end].strip().strip("'\"")
        if tail_match:
            value = text[tail_match.end():].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            item[tail_match.group(1)] = value
        rows.append(item)
    return rows


def _sms_rows(output):
    """Parse adb's content-provider rows while keeping message bodies intact."""
    rows = []
    current = []
    for line in output.splitlines():
        if re.match(r"Row:\s*\d+\s+", line):
            if current:
                rows.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        rows.append("\n".join(current))

    result = []
    for line in rows:
        match = re.match(r"Row:\s*\d+\s+(.*)", line, re.DOTALL)
        if not match:
            continue
        text = match.group(1)
        body_match = re.search(r", (body|text)=", text)
        prefix = text[:body_match.start()] if body_match else text
        fields = list(re.finditer(r"(?:^|, )([A-Za-z0-9_]+)=", prefix))
        item = {}
        for index, field in enumerate(fields):
            start = field.end()
            end = fields[index + 1].start() if index + 1 < len(fields) else len(prefix)
            item[field.group(1)] = prefix[start:end].strip().strip("'")
        if body_match:
            body = text[body_match.end():].strip()
            if len(body) >= 2 and body[0] == body[-1] and body[0] in "\"'":
                body = body[1:-1]
            item[body_match.group(1)] = body
        result.append(item)
    return result


def _error_text(error):
    return getattr(error, "message", str(error))


def _content_query_args(uri, projection, where=None, sort=None):
    """Build one safely quoted command string for Android's remote shell."""
    command = ["content", "query", "--uri", uri, "--projection", projection]
    if where:
        command.extend(("--where", where))
    if sort:
        command.extend(("--sort", sort))
    return ["shell", " ".join(shlex.quote(part) for part in command)]


class BlueZClient:
    """Live BlueZ D-Bus client with a GNOME pairing agent."""

    def __init__(self, changed, status, agent_prompt):
        self.changed = changed
        self.status = status
        self.agent_prompt = agent_prompt
        self.objects = {}
        self.bus = None
        self.agent_registered = False
        self.agent_id = 0
        self.subscriptions = []
        self.pending_agents = {}
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._subscribe_signals()
            self._register_agent()
            self.refresh()
        except GLib.Error as error:
            self.status(f"Bus Bluetooth indisponible : {_error_text(error)}", True)
            self.changed()

    def _subscribe_signals(self):
        signals = [
            (BLUEZ, OBJECT_MANAGER, "InterfacesAdded", "/", None, self._interfaces_added),
            (BLUEZ, OBJECT_MANAGER, "InterfacesRemoved", "/", None, self._interfaces_removed),
            (BLUEZ, DBUS_PROPERTIES, "PropertiesChanged", None, None, self._properties_changed),
            ("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", "/org/freedesktop/DBus", "org.bluez", self._name_owner_changed),
        ]
        for sender, iface, signal, path, arg0, handler in signals:
            self.subscriptions.append(
                self.bus.signal_subscribe(sender, iface, signal, path, arg0, Gio.DBusSignalFlags.NONE, handler, None)
            )

    def _register_agent(self):
        try:
            if not self.agent_id:
                interface = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
                self.agent_id = self.bus.register_object(BLUEZ_AGENT_PATH, interface, self._agent_call, None, None)
            self.call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", GLib.Variant("(os)", (BLUEZ_AGENT_PATH, "KeyboardDisplay")), lambda _r, e: self._agent_registered(e))
        except GLib.Error as error:
            self.status(f"Agent Bluetooth indisponible : {_error_text(error)}", True)

    def _agent_registered(self, error):
        if error:
            return
        self.agent_registered = True
        self.call("/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", GLib.Variant("(o)", (BLUEZ_AGENT_PATH,)))

    def _agent_call(self, _conn, _sender, _path, _iface, method, parameters, invocation):
        args = deep_unpack(parameters)
        if method == "Release":
            invocation.return_value(None)
        elif method == "Cancel":
            for dialog in list(self.pending_agents.values()):
                dialog.response(Gtk.ResponseType.CANCEL)
            invocation.return_value(None)
        elif method in ("RequestPinCode", "RequestPasskey", "RequestConfirmation", "RequestAuthorization", "AuthorizeService"):
            device = args[0]
            title = {
                "RequestPinCode": "Saisir le code PIN",
                "RequestPasskey": "Saisir le code de jumelage",
                "RequestConfirmation": "Confirmer le code",
                "RequestAuthorization": "Autoriser l’appareil ?",
                "AuthorizeService": "Autoriser le service Bluetooth ?",
            }[method]
            kind = "pin" if method == "RequestPinCode" else "passkey" if method == "RequestPasskey" else "confirm"
            result = "(s)" if kind == "pin" else "(u)" if kind == "passkey" else "()"
            body = f"Code : {args[1]:06d}" if method == "RequestConfirmation" else f"Service : {args[1]}" if method == "AuthorizeService" else None
            props = self.props(device, "org.bluez.Device1")
            name = props.get("Alias") or props.get("Name") or "Appareil Bluetooth"
            self.agent_prompt(invocation, kind, name, title, result, body, self.pending_agents)
        elif method in ("DisplayPinCode", "DisplayPasskey"):
            self.status(f"Code de jumelage : {args[1]}")
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.bluez.Error.NotSupported", "Méthode non prise en charge")

    def _call_done(self, connection, result, callback):
        try:
            reply = deep_unpack(connection.call_finish(result))
            if callback:
                callback(reply, None)
        except GLib.Error as error:
            if callback:
                callback(None, error)

    def call(self, path, interface, method, parameters=None, callback=None, timeout=20_000):
        if not self.bus:
            if callback:
                callback(None, RuntimeError("Bus système indisponible"))
            return
        self.bus.call(BLUEZ, path, interface, method, parameters, None, Gio.DBusCallFlags.NONE, timeout, None,
                      lambda connection, result, _data: self._call_done(connection, result, callback), None)

    def refresh(self):
        self.call("/", OBJECT_MANAGER, "GetManagedObjects", callback=self._snapshot)

    def _snapshot(self, result, error):
        if error:
            self.objects.clear()
            self.status(f"BlueZ indisponible : {_error_text(error)}", True)
        else:
            self.objects = result[0] if result else {}
        self.changed()

    def _interfaces_added(self, _bus, _sender, _path, _iface, _signal, parameters, _data):
        path, interfaces = deep_unpack(parameters)
        self.objects.setdefault(path, {}).update(interfaces)
        self.changed()

    def _interfaces_removed(self, _bus, _sender, _path, _iface, _signal, parameters, _data):
        path, interfaces = deep_unpack(parameters)
        obj = self.objects.get(path, {})
        for iface in interfaces:
            obj.pop(iface, None)
        if not obj:
            self.objects.pop(path, None)
        self.changed()

    def _properties_changed(self, _bus, _sender, path, _iface, _signal, parameters, _data):
        iface, changed, invalidated = deep_unpack(parameters)
        props = self.objects.get(path, {}).get(iface)
        if props is None:
            return
        props.update(changed)
        for key in invalidated:
            props.pop(key, None)
        self.changed()

    def _name_owner_changed(self, _bus, _sender, _path, _iface, _signal, parameters, _data):
        name, old, new = deep_unpack(parameters)
        if name != BLUEZ:
            return
        if new:
            if not self.agent_registered:
                self._register_agent()
            self.refresh()
        elif old:
            self.agent_registered = False
            self.objects.clear()
            self.changed()

    def props(self, path, interface):
        return self.objects.get(path, {}).get(interface, {})

    def set_property(self, path, interface, name, signature, value, callback=None):
        self.call(path, DBUS_PROPERTIES, "Set", GLib.Variant("(ssv)", (interface, name, GLib.Variant(signature, value))), callback)

    def close(self):
        if not self.bus:
            return
        if self.agent_registered:
            self.call("/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", GLib.Variant("(o)", (BLUEZ_AGENT_PATH,)))
        for subscription in self.subscriptions:
            self.bus.signal_unsubscribe(subscription)
        if self.agent_id:
            self.bus.unregister_object(self.agent_id)


class DroidDeskWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application)
        self.set_title("DroidDesk")
        self.set_default_size(820, 680)
        self.adb = find_executable("adb", ADB_CANDIDATES)
        self.scrcpy = find_executable("scrcpy", SCRCPY_CANDIDATES)
        self.scrcpy_server = find_executable("scrcpy-server", SCRCPY_SERVER_CANDIDATES)
        self.madbfs = find_executable("madbfs", MADBFS_CANDIDATES)
        self.devices = []
        self.device_profiles = {}
        self.contacts = []
        self.contacts_loaded = False
        self.contacts_display_limit = 100
        self.contacts_search_source = 0
        self.sms_waiting_for_contacts = False
        self.sms_canonical_addresses = {}
        self.recents = []
        self.recents_loaded = False
        self.recents_offset = 0
        self.recents_has_more = False
        self.recents_page_size = 50
        self.sms_messages = []
        self.sms_conversations = []
        self.sms_sidebar_rows = {}
        self.sms_updating_list = False
        self.sms_selected_thread = None
        self.sms_selected_number = ""
        self.sms_selected_recipients = []
        self.sms_thread_is_group = False
        self.sms_new_mode = False
        self.app_packages = []
        self.app_icons = {}
        self.app_labels = {}
        self.app_icon_cache_dir = Path(GLib.get_user_cache_dir()) / "droiddesk" / "app-icons"
        self._load_cached_app_icons()
        self.app_search_text = ""
        self.phone_profile_loading = set()
        self.embedded_scrcpy_process = None
        self.embedded_ffmpeg_process = None
        self.embedded_stream_socket = None
        self.embedded_forward_port = None
        self.embedded_stream_serial = None
        self.embedded_stream_generation = 0
        self.embedded_frame_size = (0, 0)
        self.embedded_device_size = (0, 0)
        self.embedded_drag_start = None
        self.embedded_input_lock = threading.Lock()
        self.phone_mounts = {}
        self.remote_dir = "/sdcard"
        self.file_records = {}
        self.bluetooth_busy = set()
        self.selected_adapter = None
        self.adapter_paths = []
        self._updating_bluetooth_controls = False
        self.droidlink_pinned, self.droidlink_messages = self._load_droidlink_pins()
        self._droidlink_tunnel_serial = None
        self.incoming_call_notification_key = None
        self.active_call_notification_key = None
        self.call_audio_module_ids = []
        self.call_audio_original_profile = None
        self.call_audio_phone_card = ""
        self.call_audio_busy = False
        self.call_audio_hfp_busy = False
        self.call_audio_auto_retries = 0
        self.call_audio_should_stop = False
        self.call_audio_record_sources = None
        self.call_session_active = False
        self.call_direction = "outgoing"
        self.call_recording_enabled = self._load_call_recording_enabled()
        self.call_recording_process = None
        self.call_recording_path = None
        self.photo_preview_dir = None
        self.photo_preview_window = None
        self.external_photo_dirs = []
        self.gallery_photos = []
        self.gallery_loaded = False
        self.gallery_cards = {}
        self.gallery_requested_thumbnails = set()
        self.gallery_thumbnail_cache_paths = {}
        self.gallery_thumbnail_cache_dir = None
        self.gallery_selected = None
        self.busy = False
        self._build_ui()
        self.connect("close-request", self._on_window_close_request)
        self.bluez = BlueZClient(self.update_bluetooth_view, self._set_status, self._agent_prompt)
        try:
            self.droidlink_bridge = DroidLinkBridge(
                on_change=self._droidlink_state_changed,
                on_event=self._droidlink_received,
            )
        except OSError as error:
            self.droidlink_bridge = None
            self._set_status(f"DroidLink indisponible : {error}", True)
        self.droidlink_timer = GLib.timeout_add_seconds(1, self._droidlink_timer_tick)
        self.connect("close-request", self._on_close)
        self._update_controls()
        self.refresh_devices()
        self.update_bluetooth_view()

    def _build_ui(self):
        self.add_css_class("droiddesk-window")
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._css_provider = provider

        toolbar = Adw.ToolbarView.new()
        header = Adw.HeaderBar.new()
        header.set_title_widget(Adw.WindowTitle.new("DroidDesk", "Téléphone Android sur Debian"))

        phone_badge = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        phone_badge.add_css_class("card")
        self.phone_thumb = Gtk.DrawingArea()
        self.phone_thumb.set_content_width(34)
        self.phone_thumb.set_content_height(52)
        self.phone_thumb.set_draw_func(self._draw_phone_thumbnail)
        phone_badge.append(self.phone_thumb)
        profile_labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.phone_name_label = Gtk.Label(label="Aucun téléphone", xalign=0)
        self.phone_name_label.add_css_class("heading")
        self.phone_subtitle_label = Gtk.Label(label="Connectez un appareil ADB", xalign=0)
        self.phone_subtitle_label.add_css_class("dim-label")
        self.phone_battery_label = Gtk.Label(label="", xalign=0)
        self.phone_battery_label.add_css_class("dim-label")
        self.phone_battery_label.set_visible(False)
        self.phone_storage_label = Gtk.Label(label="", xalign=0)
        self.phone_storage_label.add_css_class("dim-label")
        self.phone_storage_label.set_visible(False)
        profile_labels.append(self.phone_name_label)
        profile_labels.append(self.phone_subtitle_label)
        profile_labels.append(self.phone_battery_label)
        profile_labels.append(self.phone_storage_label)
        phone_badge.append(profile_labels)
        header.pack_start(phone_badge)

        self.device_profile_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.device_profile_refresh_button.set_tooltip_text("Actualiser les informations du téléphone")
        self.device_profile_refresh_button.connect("clicked", self._refresh_device_profile)
        header.pack_start(self.device_profile_refresh_button)

        self.device_picker = Gtk.DropDown.new_from_strings(["Recherche du téléphone…"])
        self.device_model = self.device_picker.get_model()
        self.device_picker.set_tooltip_text("Téléphone Android connecté à ADB")
        self.device_picker.set_size_request(175, -1)
        self._device_selected_handler = self.device_picker.connect(
            "notify::selected", self._on_device_selected
        )
        header.pack_start(self.device_picker)

        self.device_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.device_refresh_button.set_tooltip_text("Actualiser les appareils ADB")
        self.device_refresh_button.connect("clicked", lambda *_: self.refresh_devices())
        header.pack_start(self.device_refresh_button)
        about_button = Gtk.Button.new_from_icon_name("help-about-symbolic")
        about_button.set_tooltip_text("À propos")
        about_button.connect("clicked", self._show_about)
        header.pack_end(about_button)
        toolbar.add_top_bar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.navigation_rows = {}
        self._last_navigation_section = None
        self.navigation_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.navigation_panel.add_css_class("app-sidebar")
        self.navigation_panel.set_size_request(190, -1)
        self.navigation_panel.set_margin_top(8)
        self.navigation_panel.set_margin_bottom(8)
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand.add_css_class("navigation-brand")
        brand_icon = Gtk.Image.new_from_icon_name("phone-symbolic")
        brand_icon.set_pixel_size(30)
        brand_icon.add_css_class("brand-icon")
        brand.append(brand_icon)
        brand_labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brand_title = Gtk.Label(label="DroidDesk", xalign=0)
        brand_title.add_css_class("heading")
        brand_subtitle = Gtk.Label(label="Votre Android, dans GNOME", xalign=0)
        brand_subtitle.add_css_class("dim-label")
        brand_labels.append(brand_title)
        brand_labels.append(brand_subtitle)
        brand.append(brand_labels)
        self.navigation_panel.append(brand)
        self.navigation_list = Gtk.ListBox()
        self.navigation_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.navigation_list.add_css_class("navigation-list")
        self.navigation_list.connect("row-selected", self._on_navigation_selected)
        navigation_scroll = Gtk.ScrolledWindow()
        navigation_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        navigation_scroll.set_vexpand(True)
        navigation_scroll.set_child(self.navigation_list)
        self.navigation_panel.append(navigation_scroll)
        content.append(self.navigation_panel)
        content.append(self.stack)
        root.append(content)

        self.status_label = Gtk.Label(label="Connexion à ADB…", xalign=0)
        self.status_label.set_wrap(True)
        self.status_label.set_selectable(True)
        self.status_label.set_margin_start(18)
        self.status_label.set_margin_end(18)
        self.status_label.set_margin_top(8)
        self.status_label.set_margin_bottom(10)
        self.status_label.add_css_class("dim-label")
        self.status_label.add_css_class("app-status")
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        root.append(self.status_label)
        toolbar.set_content(root)
        self.set_content(toolbar)

        self.calls_page = self._build_calls_page()
        self.contacts_page = self._build_contacts_page()
        self.sms_page = self._build_sms_page()
        self.screen_page = self._build_screen_page()
        self.gallery_page = self._build_gallery_page()
        self.files_page = self._build_files_page()
        self.bluetooth_page = self._build_bluetooth_page()
        self.adb_page = self._build_adb_page()
        self.droidlink_page = self._build_droidlink_page()
        self._add_stack_page(self.calls_page, "calls", "Appels", "call-start-symbolic", "Téléphone")
        self._add_stack_page(self.contacts_page, "contacts", "Contacts", "avatar-default-symbolic", "Téléphone")
        self._add_stack_page(self.sms_page, "sms", "Messages", "mail-message-new-symbolic", "Téléphone")
        self._add_stack_page(self.screen_page, "screen", "Écran et apps", "applications-graphics-symbolic", "Applications")
        self._add_stack_page(self.gallery_page, "gallery", "Galerie", "image-x-generic-symbolic", "Applications")
        self._add_stack_page(self.files_page, "files", "Fichiers", "folder-symbolic", "Applications")
        self._add_stack_page(self.bluetooth_page, "bluetooth", "Bluetooth", "bluetooth-symbolic", "Connexion")
        self._add_stack_page(self.adb_page, "adb", "ADB", "utilities-terminal-symbolic", "Connexion")
        self._add_stack_page(self.droidlink_page, "droidlink", "DroidLink", "network-wireless-symbolic", "Connexion")
        self.stack.connect("notify::visible-child-name", self._on_page_changed)
        self.navigation_list.select_row(self.navigation_rows.get("calls"))

    def _build_droidlink_page(self):
        page = self._page()
        page.append(self._heading(
            "DroidLink",
            "Compagnon Android : notifications et actions dans GNOME, batterie, photos et vraies icônes. Les appels et SMS restent dans leurs applications Android habituelles.",
        ))

        pairing = Adw.PreferencesGroup.new()
        pairing.set_title("Téléphone compagnon")
        pairing.set_description("Le code est à usage unique et expire après deux minutes.")
        self.droidlink_status = Gtk.Label(label="Aucun téléphone DroidLink associé.", xalign=0)
        self.droidlink_status.set_wrap(True)
        self.droidlink_status.add_css_class("dim-label")
        status_row = Adw.ActionRow.new()
        status_row.set_title("État")
        status_row.add_suffix(self.droidlink_status)
        pairing.add(status_row)

        self.droidlink_code_label = Gtk.Label(label="—")
        self.droidlink_code_label.add_css_class("title-1")
        self.droidlink_code_label.add_css_class("monospace")
        self.droidlink_code_label.set_selectable(True)
        code_row = Adw.ActionRow.new()
        code_row.set_title("Code d’association")
        code_row.set_subtitle("Saisis-le dans l’application DroidLink sur Android.")
        code_row.add_suffix(self.droidlink_code_label)
        pairing.add(code_row)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.droidlink_pair_button = Gtk.Button(label="Démarrer l’appairage")
        self.droidlink_pair_button.add_css_class("suggested-action")
        self.droidlink_pair_button.connect("clicked", self._start_droidlink_pairing)
        actions.append(self.droidlink_pair_button)
        self.droidlink_disconnect_button = Gtk.Button(label="Dissocier / retirer le tunnel")
        self.droidlink_disconnect_button.connect("clicked", self._disconnect_droidlink)
        actions.append(self.droidlink_disconnect_button)
        page.append(pairing)
        page.append(actions)

        page.append(self._heading("Historique des alertes", "Les alertes apparaissent aussi dans le centre de notifications GNOME. Cette vue garde les actions et les éléments épinglés."))
        history_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.droidlink_clear_button = Gtk.Button(label="Tout effacer")
        self.droidlink_clear_button.set_tooltip_text("Effacer l’historique et ignorer les alertes actives sur le téléphone")
        self.droidlink_clear_button.connect("clicked", self._clear_droidlink_history)
        history_actions.append(self.droidlink_clear_button)
        page.append(history_actions)
        self.droidlink_empty = Gtk.Label(label="Aucune notification reçue.", xalign=0)
        self.droidlink_empty.add_css_class("dim-label")
        page.append(self.droidlink_empty)
        self.droidlink_list = Gtk.ListBox()
        self.droidlink_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.droidlink_list.add_css_class("boxed-list")
        page.append(self.droidlink_list)

        privacy = Gtk.Label(
            label="L’accès aux notifications maintient le canal local utilisé pour les commandes du bureau. Les images et le rôle Téléphone demandent leurs autorisations Android dédiées. Les 50 alertes les plus récentes sont gardées localement; aucune connexion cloud n’est utilisée.",
            xalign=0,
        )
        privacy.set_wrap(True)
        privacy.add_css_class("dim-label")
        page.append(privacy)
        self._render_droidlink_messages()
        return page

    def _add_stack_page(self, child, name, title, icon_name, section):
        if section != self._last_navigation_section:
            section_row = Gtk.ListBoxRow()
            section_row.set_selectable(False)
            section_row.set_activatable(False)
            section_label = Gtk.Label(label=section.upper(), xalign=0)
            section_label.add_css_class("navigation-section")
            section_row.set_child(section_label)
            self.navigation_list.append(section_row)
            self._last_navigation_section = section

        nav_row = Gtk.ListBoxRow()
        nav_row.set_name(name)
        nav_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        nav_content.set_margin_start(10)
        nav_content.set_margin_end(10)
        nav_icon = Gtk.Image.new_from_icon_name(icon_name)
        nav_icon.set_pixel_size(18)
        nav_content.append(nav_icon)
        nav_label = Gtk.Label(label=title, xalign=0)
        nav_label.set_hexpand(True)
        nav_content.append(nav_label)
        nav_row.set_child(nav_content)
        self.navigation_list.append(nav_row)
        self.navigation_rows[name] = nav_row

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(child)
        self.stack.add_titled(scroll, name, title)

    def _on_navigation_selected(self, _list, row):
        if row:
            self.stack.set_visible_child_name(row.get_name())

    def _sync_navigation_selection(self):
        page_name = self.stack.get_visible_child_name()
        row = self.navigation_rows.get(page_name)
        if row and self.navigation_list.get_selected_row() is not row:
            self.navigation_list.select_row(row)

    def _page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(26)
        page.set_margin_end(26)
        return page

    @staticmethod
    def _heading(text, description=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("page-heading")
        title = Gtk.Label(label=text, xalign=0)
        title.add_css_class("page-title")
        box.append(title)
        if description:
            subtitle = Gtk.Label(label=description, xalign=0)
            subtitle.set_wrap(True)
            subtitle.add_css_class("page-description")
            box.append(subtitle)
        return box

    def _draw_phone_thumbnail(self, _area, cr, width, height):
        active = self._active_device()
        profile = self.device_profiles.get(active[0], {}) if active else {}
        brand = (profile.get("manufacturer") or "").lower()
        colors = {
            "samsung": (0.10, 0.29, 0.62),
            "google": (0.20, 0.45, 0.83),
            "apple": (0.20, 0.22, 0.26),
            "xiaomi": (0.91, 0.34, 0.10),
            "motorola": (0.10, 0.42, 0.37),
            "oneplus": (0.75, 0.12, 0.15),
            "huawei": (0.68, 0.10, 0.16),
            "sony": (0.20, 0.22, 0.26),
        }
        color = next((value for key, value in colors.items() if key in brand), (0.20, 0.44, 0.76))
        scale = min(width / 34.0, height / 52.0)
        xoff = (width - 34 * scale) / 2
        yoff = (height - 52 * scale) / 2
        cr.save()
        cr.translate(xoff, yoff)
        cr.scale(scale, scale)
        cr.set_source_rgb(0.10, 0.11, 0.13)
        cr.arc(17, 8, 6, 3.14, 6.28)
        cr.arc(26, 8, 6, 4.71, 7.85)
        cr.arc(26, 44, 6, 0, 1.57)
        cr.arc(8, 44, 6, 1.57, 3.14)
        cr.close_path()
        cr.fill()
        cr.set_source_rgb(*color)
        cr.rectangle(4, 8, 26, 36)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.rectangle(4, 8, 26, 12)
        cr.fill()
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(7)
        cr.set_source_rgba(1, 1, 1, 0.88)
        cr.move_to(14, 29)
        cr.show_text((brand[:1] or "D").upper())
        cr.set_source_rgb(0.87, 0.90, 0.95)
        cr.arc(17, 47, 1.4, 0, 6.28)
        cr.fill()
        cr.restore()

    def _build_contacts_page(self):
        page = self._page()
        page.append(self._heading(
            "Contacts",
            "Les contacts restent en cache pendant la session. Les numéros sont regroupés sous une fiche et 100 contacts s’affichent à la fois.",
        ))
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.contact_search = Gtk.SearchEntry()
        self.contact_search.set_hexpand(True)
        self.contact_search.set_placeholder_text("Rechercher un nom ou un numéro")
        self.contact_search.connect("search-changed", self._contacts_search_changed)
        bar.append(self.contact_search)
        self.contacts_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.contacts_refresh_button.set_tooltip_text("Actualiser les contacts")
        self.contacts_refresh_button.connect("clicked", self._refresh_contacts)
        bar.append(self.contacts_refresh_button)
        page.append(bar)
        self.contacts_status = Gtk.Label(label="Connectez un téléphone ADB pour charger les contacts.", xalign=0)
        self.contacts_status.add_css_class("dim-label")
        self.contacts_status.add_css_class("sms-status-card")
        page.append(self.contacts_status)
        self.contact_list = Gtk.ListBox()
        self.contact_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.contact_list.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add_css_class("list-panel")
        scroll.set_child(self.contact_list)
        page.append(scroll)
        self.contacts_more_button = Gtk.Button(label="Afficher 100 autres contacts")
        self.contacts_more_button.add_css_class("list-footer")
        self.contacts_more_button.connect("clicked", self._show_more_contacts)
        self.contacts_more_button.set_visible(False)
        page.append(self.contacts_more_button)
        return page

    def _build_sms_page(self):
        page = self._page()
        page.append(self._heading(
            "Messages",
            "SMS et MMS lus depuis le téléphone. Google Messages ne publie pas l’historique RCS, y compris les discussions chiffrées, dans la base Android accessible à DroidDesk.",
        ))
        self.sms_status = Gtk.Label(label="Lecture des messages enregistrés sur Android…", xalign=0)
        self.sms_status.add_css_class("dim-label")
        self.sms_status.add_css_class("sms-status-card")
        page.append(self.sms_status)

        workspace = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        workspace.set_wide_handle(True)
        workspace.set_vexpand(True)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.add_css_class("sms-sidebar")
        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sidebar_title = Gtk.Label(label="Conversations", xalign=0)
        sidebar_title.add_css_class("heading")
        sidebar_title.set_hexpand(True)
        sidebar_header.append(sidebar_title)
        self.sms_new_button = Gtk.Button.new_from_icon_name("document-new-symbolic")
        self.sms_new_button.set_tooltip_text("Nouveau SMS")
        self.sms_new_button.connect("clicked", self._start_new_sms)
        sidebar_header.append(self.sms_new_button)
        self.sms_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.sms_refresh_button.set_tooltip_text("Actualiser les SMS")
        self.sms_refresh_button.connect("clicked", self._refresh_sms)
        sidebar_header.append(self.sms_refresh_button)
        sidebar.append(sidebar_header)
        self.sms_search = Gtk.SearchEntry()
        self.sms_search.set_placeholder_text("Rechercher une conversation")
        self.sms_search.connect("search-changed", self._on_sms_search)
        sidebar.append(self.sms_search)
        self.sms_sidebar_list = Gtk.ListBox()
        self.sms_sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sms_sidebar_list.set_activate_on_single_click(True)
        self.sms_sidebar_list.add_css_class("boxed-list")
        self.sms_sidebar_list.connect("row-selected", self._sms_sidebar_selected)
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_vexpand(True)
        sidebar_scroll.set_child(self.sms_sidebar_list)
        sidebar.append(sidebar_scroll)
        self.sms_sidebar_empty = Gtk.Label(label="Aucune conversation", xalign=0.5)
        self.sms_sidebar_empty.add_css_class("dim-label")
        self.sms_sidebar_empty.set_margin_top(18)
        sidebar.append(self.sms_sidebar_empty)
        workspace.set_start_child(sidebar)
        workspace.set_resize_start_child(False)
        workspace.set_shrink_start_child(True)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail.set_vexpand(True)
        self.sms_thread_title = Gtk.Label(label="Choisis une conversation", xalign=0)
        self.sms_thread_title.add_css_class("heading")
        detail.append(self.sms_thread_title)
        self.sms_thread_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sms_thread_list.set_margin_top(8)
        self.sms_thread_list.set_margin_bottom(8)
        self.sms_thread_list.set_margin_start(8)
        self.sms_thread_list.set_margin_end(8)
        self.sms_thread_list.set_valign(Gtk.Align.START)
        thread_scroll = Gtk.ScrolledWindow()
        self.sms_thread_scroll = thread_scroll
        thread_scroll.set_vexpand(True)
        thread_scroll.add_css_class("card")
        thread_scroll.set_child(self.sms_thread_list)
        detail.append(thread_scroll)
        workspace.set_end_child(detail)
        workspace.set_resize_end_child(True)
        workspace.set_shrink_end_child(True)
        workspace.set_position(250)
        page.append(workspace)

        compose = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        compose.add_css_class("sms-composer")
        self.sms_recipient_label = Gtk.Label(label="Choisis une conversation pour répondre", xalign=0)
        self.sms_recipient_label.add_css_class("dim-label")
        compose.append(self.sms_recipient_label)
        self.sms_number_entry = Gtk.Entry()
        self.sms_number_entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        self.sms_number_entry.set_placeholder_text("Numéro du destinataire")
        self.sms_number_entry.set_visible(False)
        compose.append(self.sms_number_entry)
        self.sms_body_view = Gtk.TextView()
        self.sms_body_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.sms_body_view.set_left_margin(8)
        self.sms_body_view.set_right_margin(8)
        self.sms_body_view.set_top_margin(7)
        self.sms_body_view.set_bottom_margin(7)
        self.sms_body_view.set_size_request(-1, 58)
        self.sms_body_view.add_css_class("sms-composer-input")
        body_scroll = Gtk.ScrolledWindow()
        body_scroll.set_min_content_height(58)
        body_scroll.set_max_content_height(94)
        body_scroll.add_css_class("sms-composer-input")
        body_scroll.set_child(self.sms_body_view)
        compose_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        body_scroll.set_hexpand(True)
        compose_row.append(body_scroll)
        self.sms_open_button = Gtk.Button(label="Ouvrir Messages")
        self.sms_open_button.add_css_class("suggested-action")
        self.sms_open_button.connect("clicked", self._send_sms_to_phone)
        compose_row.append(self.sms_open_button)
        compose.append(compose_row)
        detail.append(compose)
        return page

    def _build_bluetooth_page(self):
        page = self._page()
        page.append(self._heading("Bluetooth", "Appairez le téléphone et vérifiez le profil mains libres utilisé pour les appels."))
        adapter_group = Adw.PreferencesGroup.new()
        adapter_group.set_title("Contrôleur Bluetooth")
        self.bt_adapter_row = Adw.ComboRow.new()
        self.bt_adapter_row.set_title("Adaptateur")
        self.bt_adapter_model = Gtk.StringList.new(["Chargement…"])
        self.bt_adapter_row.set_model(self.bt_adapter_model)
        self.bt_adapter_row.connect("notify::selected", self._on_bt_adapter_selected)
        adapter_group.add(self.bt_adapter_row)
        self.bt_power_row = Adw.SwitchRow.new()
        self.bt_power_row.set_title("Bluetooth activé")
        self.bt_power_row.connect("notify::active", self._on_bt_power)
        adapter_group.add(self.bt_power_row)
        self.bt_discoverable_row = Adw.SwitchRow.new()
        self.bt_discoverable_row.set_title("Visible par les appareils")
        self.bt_discoverable_row.connect("notify::active", self._on_bt_discoverable)
        adapter_group.add(self.bt_discoverable_row)
        self.bt_pairable_row = Adw.SwitchRow.new()
        self.bt_pairable_row.set_title("Appairage autorisé")
        self.bt_pairable_row.connect("notify::active", self._on_bt_pairable)
        adapter_group.add(self.bt_pairable_row)
        page.append(adapter_group)

        bt_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.bt_scan_button = Gtk.Button(label="Rechercher des appareils")
        self.bt_scan_button.add_css_class("suggested-action")
        self.bt_scan_button.connect("clicked", self._toggle_bt_scan)
        bt_actions.append(self.bt_scan_button)
        self.bt_refresh_button = Gtk.Button(label="Actualiser")
        self.bt_refresh_button.connect("clicked", lambda *_: self.bluez.refresh())
        bt_actions.append(self.bt_refresh_button)
        page.append(bt_actions)

        self.bt_search = Gtk.SearchEntry()
        self.bt_search.set_placeholder_text("Filtrer les appareils par nom ou adresse")
        self.bt_search.connect("search-changed", lambda *_: self.update_bluetooth_view())
        page.append(self.bt_search)
        self.bt_device_list = Gtk.ListBox()
        self.bt_device_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.bt_device_list.add_css_class("boxed-list")
        bt_scroll = Gtk.ScrolledWindow()
        bt_scroll.set_vexpand(True)
        bt_scroll.set_child(self.bt_device_list)
        page.append(bt_scroll)
        self.bt_hint = Gtk.Label(
            label="Pour parler et écouter depuis le PC, Android doit être appairé à Debian comme casque Bluetooth mains libres (HFP/HSP). Le profil audio média A2DP seul ne transporte pas le micro d’appel.", xalign=0
        )
        self.bt_hint.set_wrap(True)
        self.bt_hint.add_css_class("dim-label")
        page.append(self.bt_hint)
        return page

    def _build_adb_page(self):
        page = self._page()
        page.append(self._heading("ADB et connexion", "Connexion, maintenance, diagnostic et console Android."))
        self.adb_status_card = Adw.PreferencesGroup.new()
        self.adb_status_card.set_title("Débogage Android")
        self.adb_status_card.set_description("Les actions appareil exigent un téléphone autorisé dans ADB.")
        self.adb_path_row = Adw.ActionRow.new()
        self.adb_path_row.set_title("ADB")
        self.adb_path_row.set_subtitle(self.adb or "Introuvable")
        self.adb_status_card.add(self.adb_path_row)
        page.append(self.adb_status_card)

        controls = Gtk.FlowBox()
        controls.set_selection_mode(Gtk.SelectionMode.NONE)
        controls.set_min_children_per_line(1)
        controls.set_max_children_per_line(2)
        controls.set_row_spacing(6)
        controls.set_column_spacing(6)
        self.adb_start_button = Gtk.Button(label="Démarrer ADB")
        self.adb_start_button.connect("clicked", lambda *_: self._run_adb_command(["start-server"], "Démarrage du serveur ADB…", lambda r, e: self._command_status(r, e, "Serveur ADB démarré."), needs_device=False))
        controls.append(self.adb_start_button)
        self.adb_refresh_button = Gtk.Button(label="Actualiser les appareils")
        self.adb_refresh_button.connect("clicked", lambda *_: self.refresh_devices())
        controls.append(self.adb_refresh_button)
        self.adb_reconnect_button = Gtk.Button(label="Reconnecter hors ligne")
        self.adb_reconnect_button.connect("clicked", lambda *_: self._run_adb_command(["reconnect", "offline"], "Reconnexion des appareils hors ligne…", self._command_output_result, needs_device=False))
        controls.append(self.adb_reconnect_button)
        page.append(controls)

        wireless = Adw.PreferencesGroup.new()
        wireless.set_title("Débogage sans fil")
        wireless.set_description("Sur Android 11+, ouvrez Débogage sans fil et utilisez les codes affichés par le téléphone.")
        self.adb_pair_button = Gtk.Button(label="Appairer avec un code…")
        self.adb_pair_button.connect("clicked", self._ask_adb_pair)
        pair_row = Adw.ActionRow.new()
        pair_row.set_title("Nouvel appairage")
        pair_row.add_suffix(self.adb_pair_button)
        wireless.add(pair_row)
        self.adb_connect_button = Gtk.Button(label="Connecter à une adresse…")
        self.adb_connect_button.connect("clicked", self._ask_adb_connect)
        connect_row = Adw.ActionRow.new()
        connect_row.set_title("Connexion TCP/IP")
        connect_row.add_suffix(self.adb_connect_button)
        wireless.add(connect_row)
        page.append(wireless)

        server_group = Adw.PreferencesGroup.new()
        server_group.set_title("Alimentation et redémarrage")
        server_group.set_description("Redémarrer ou éteindre le téléphone. Une confirmation sera demandée.")
        actions = Gtk.FlowBox()
        actions.set_selection_mode(Gtk.SelectionMode.NONE)
        actions.set_min_children_per_line(1)
        actions.set_max_children_per_line(2)
        actions.set_row_spacing(6)
        actions.set_column_spacing(6)
        for label, action in (
            ("Redémarrer", "system"),
            ("Récupération", "recovery"),
            ("Bootloader", "bootloader"),
            ("Éteindre", "poweroff"),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, value=action: self._confirm_device_action(value))
            if action == "poweroff":
                button.add_css_class("destructive-action")
            actions.append(button)
            if action == "system":
                self.adb_reboot_button = button
            elif action == "recovery":
                self.adb_recovery_button = button
            elif action == "bootloader":
                self.adb_bootloader_button = button
            else:
                self.adb_poweroff_button = button
        action_row = Adw.ActionRow.new()
        action_row.set_title("Commandes d’alimentation")
        action_row.add_suffix(actions)
        server_group.add(action_row)

        key_actions = Gtk.FlowBox()
        key_actions.set_selection_mode(Gtk.SelectionMode.NONE)
        key_actions.set_min_children_per_line(1)
        key_actions.set_max_children_per_line(2)
        key_actions.set_row_spacing(6)
        key_actions.set_column_spacing(6)
        for label, key in (("Réveiller", "KEYCODE_WAKEUP"), ("Accueil", "KEYCODE_HOME"), ("Retour", "KEYCODE_BACK"), ("Verrouiller", "KEYCODE_POWER")):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, value=key: self._send_phone_key(value))
            key_actions.append(button)
            if key == "KEYCODE_WAKEUP":
                self.adb_wake_button = button
            elif key == "KEYCODE_HOME":
                self.adb_home_button = button
            elif key == "KEYCODE_BACK":
                self.adb_back_button = button
            else:
                self.adb_lock_button = button
        key_row = Adw.ActionRow.new()
        key_row.set_title("Touches système")
        key_row.add_suffix(key_actions)
        server_group.add(key_row)
        page.append(server_group)

        server_group = Adw.PreferencesGroup.new()
        server_group.set_title("Console shell Android")
        server_group.set_description("Exécute des commandes avec l’utilisateur shell ADB; cela ne donne pas root.")
        console_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.adb_shell_entry = Gtk.Entry()
        self.adb_shell_entry.set_hexpand(True)
        self.adb_shell_entry.set_placeholder_text("Ex. dumpsys battery · pm list packages · getprop")
        self.adb_shell_entry.set_activates_default(True)
        self.adb_shell_entry.connect("activate", self._run_shell_console)
        console_controls.append(self.adb_shell_entry)
        self.adb_shell_run_button = Gtk.Button(label="Exécuter")
        self.adb_shell_run_button.add_css_class("suggested-action")
        self.adb_shell_run_button.connect("clicked", self._run_shell_console)
        console_controls.append(self.adb_shell_run_button)
        shell_row = Adw.ActionRow.new()
        shell_row.set_title("Commande")
        shell_row.add_suffix(console_controls)
        server_group.add(shell_row)
        self.adb_output = Gtk.TextView()
        self.adb_output.set_editable(False)
        self.adb_output.set_cursor_visible(False)
        self.adb_output.set_monospace(True)
        self.adb_output.set_wrap_mode(Gtk.WrapMode.NONE)
        self.adb_output_buffer = self.adb_output.get_buffer()
        self.adb_output_buffer.set_text("Les résultats ADB apparaîtront ici.")
        output_scroll = Gtk.ScrolledWindow()
        output_scroll.set_min_content_height(0)
        output_scroll.set_max_content_height(300)
        output_scroll.set_child(self.adb_output)
        server_group.add(output_scroll)
        page.append(server_group)

        diagnostics = Adw.PreferencesGroup.new()
        diagnostics.set_title("Diagnostics")
        diagnostic_buttons = Gtk.FlowBox()
        diagnostic_buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        diagnostic_buttons.set_min_children_per_line(1)
        diagnostic_buttons.set_max_children_per_line(2)
        diagnostic_buttons.set_row_spacing(6)
        diagnostic_buttons.set_column_spacing(6)
        self.adb_props_button = Gtk.Button(label="Propriétés Android")
        self.adb_props_button.connect("clicked", lambda *_: self._run_adb_command(["shell", "getprop"], "Lecture des propriétés Android…", self._command_output_result))
        diagnostic_buttons.append(self.adb_props_button)
        self.adb_logcat_button = Gtk.Button(label="Derniers logs")
        self.adb_logcat_button.connect("clicked", lambda *_: self._run_adb_command(["logcat", "-d", "-t", "250"], "Lecture des 250 dernières lignes de logcat…", self._command_output_result, timeout=30))
        diagnostic_buttons.append(self.adb_logcat_button)
        self.adb_screenshot_button = Gtk.Button(label="Capture d’écran…")
        self.adb_screenshot_button.connect("clicked", self._save_screenshot)
        diagnostic_buttons.append(self.adb_screenshot_button)
        self.adb_version_button = Gtk.Button(label="Version ADB")
        self.adb_version_button.connect("clicked", lambda *_: self._run_adb_command(["version"], "Lecture de la version ADB…", self._command_output_result, needs_device=False))
        diagnostic_buttons.append(self.adb_version_button)
        diagnostic_row = Adw.ActionRow.new()
        diagnostic_row.set_title("Informations et journaux")
        diagnostic_row.add_suffix(diagnostic_buttons)
        diagnostics.add(diagnostic_row)
        page.append(diagnostics)

        server_group = Adw.PreferencesGroup.new()
        server_group.set_title("Serveur ADB")
        self.adb_stop_button = Gtk.Button(label="Arrêter le serveur ADB")
        self.adb_stop_button.connect("clicked", lambda *_: self._run_adb_command(["kill-server"], "Arrêt du serveur ADB…", lambda r, e: self._command_status(r, e, "Serveur ADB arrêté."), needs_device=False))
        stop_row = Adw.ActionRow.new()
        stop_row.set_title("Redémarrer en cas de blocage")
        stop_row.add_suffix(self.adb_stop_button)
        server_group.add(stop_row)
        page.append(server_group)
        return page

    def _build_calls_page(self):
        page = self._page()
        page.append(self._heading("Appels"))

        outgoing = Adw.PreferencesGroup.new()
        outgoing.set_title("Appeler")
        self.number_entry = Gtk.Entry()
        self.number_entry.set_placeholder_text("Numéro de téléphone")
        self.number_entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        self.number_entry.set_activates_default(True)
        self.number_entry.connect("activate", self._place_call)
        number_row = Adw.ActionRow.new()
        number_row.set_title("Numéro")
        number_row.add_suffix(self.number_entry)
        number_row.set_activatable_widget(self.number_entry)
        outgoing.add(number_row)
        self.dialpad = Gtk.Grid()
        self.dialpad.set_row_spacing(7)
        self.dialpad.set_column_spacing(7)
        self.dialpad.set_halign(Gtk.Align.CENTER)
        dial_keys = (
            ("1", ""), ("2", "ABC"), ("3", "DEF"),
            ("4", "GHI"), ("5", "JKL"), ("6", "MNO"),
            ("7", "PQRS"), ("8", "TUV"), ("9", "WXYZ"),
            ("*", ""), ("0", "+"), ("#", ""),
        )
        for index, (digit, letters) in enumerate(dial_keys):
            key = Gtk.Button()
            key.add_css_class("pill")
            key.set_size_request(76, 58)
            key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            number = Gtk.Label(label=digit)
            number.add_css_class("title-3")
            key_box.append(number)
            if letters:
                small = Gtk.Label(label=letters)
                small.add_css_class("caption")
                key_box.append(small)
            key.set_child(key_box)
            key.connect("clicked", lambda _button, value=digit: self._append_digit(value))
            self.dialpad.attach(key, index % 3, index // 3, 1, 1)
        dialpad_label = Gtk.Label(label="Pavé numérique", xalign=0)
        dialpad_label.add_css_class("heading")
        outgoing.add(dialpad_label)
        outgoing.add(self.dialpad)
        self.dial_button = Gtk.Button(label="Appeler depuis GNOME")
        self.dial_button.add_css_class("suggested-action")
        self.dial_button.connect("clicked", self._call_button_clicked)
        dial_row = Adw.ActionRow.new()
        dial_row.set_title("Lancer l’appel")
        dial_row.add_suffix(self.dial_button)
        outgoing.add(dial_row)
        outgoing.set_hexpand(True)
        outgoing.set_vexpand(True)

        recent_group = Adw.PreferencesGroup.new()
        recent_group.set_title("Appels récents")
        recent_group.set_hexpand(True)
        recent_group.set_vexpand(True)
        recent_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.recents_status = Gtk.Label(label="50 derniers appels", xalign=0)
        self.recents_status.set_hexpand(True)
        self.recents_status.set_wrap(True)
        self.recents_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.recents_status.add_css_class("dim-label")
        recent_controls.append(self.recents_status)
        self.recents_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.recents_refresh_button.set_tooltip_text("Actualiser les appels récents")
        self.recents_refresh_button.connect("clicked", self._refresh_recents)
        recent_controls.append(self.recents_refresh_button)
        self.recents_open_button = Gtk.Button.new_from_icon_name("document-open-symbolic")
        self.recents_open_button.set_tooltip_text("Ouvrir le journal sur Android")
        self.recents_open_button.connect("clicked", self._open_android_call_log)
        recent_controls.append(self.recents_open_button)
        recent_group.add(recent_controls)
        self.recent_list = Gtk.ListBox()
        self.recent_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recent_list.add_css_class("boxed-list")
        recent_scroll = Gtk.ScrolledWindow()
        recent_scroll.set_min_content_height(360)
        recent_scroll.set_vexpand(True)
        recent_scroll.set_child(self.recent_list)
        recent_group.add(recent_scroll)
        self.recents_more_button = Gtk.Button(label="Charger 50 suivants")
        self.recents_more_button.add_css_class("list-footer")
        self.recents_more_button.connect("clicked", self._load_more_recents)
        self.recents_more_button.set_visible(False)
        recent_group.add(self.recents_more_button)

        call_workspace = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        call_workspace.set_hexpand(True)
        call_workspace.set_vexpand(True)
        call_workspace.set_homogeneous(True)
        call_workspace.append(outgoing)
        call_workspace.append(recent_group)
        page.append(call_workspace)

        incoming = Adw.PreferencesGroup.new()
        incoming.set_title("Appel en cours ou entrant")
        controls = Gtk.FlowBox()
        controls.set_selection_mode(Gtk.SelectionMode.NONE)
        controls.set_min_children_per_line(1)
        controls.set_max_children_per_line(2)
        controls.set_row_spacing(6)
        controls.set_column_spacing(6)
        self.answer_button = Gtk.Button(label="Répondre")
        self.answer_button.connect("clicked", self._answer_call)
        self.answer_button.set_visible(False)
        self.reject_call_button = Gtk.Button(label="Refuser")
        self.reject_call_button.add_css_class("destructive-action")
        self.reject_call_button.connect("clicked", self._reject_call)
        self.reject_call_button.set_visible(False)
        controls.append(self.answer_button)
        controls.append(self.reject_call_button)
        incoming.add(controls)
        self.call_link_status = Gtk.Label(label="Les commandes utilisent l’application Téléphone Android par défaut.", xalign=0)
        self.call_link_status.set_wrap(True)
        self.call_link_status.add_css_class("dim-label")
        incoming.add(self.call_link_status)
        audio_actions = Gtk.FlowBox()
        audio_actions.set_selection_mode(Gtk.SelectionMode.NONE)
        audio_actions.set_min_children_per_line(1)
        audio_actions.set_max_children_per_line(1)
        audio_actions.set_row_spacing(5)
        self.call_audio_bluetooth_button = Gtk.Button(label="Relier l’audio")
        self.call_audio_bluetooth_button.add_css_class("suggested-action")
        self.call_audio_bluetooth_button.connect("clicked", self._toggle_call_audio_bridge)
        audio_actions.append(self.call_audio_bluetooth_button)
        self.call_audio_hfp_button = Gtk.Button(label="Activer HFP")
        self.call_audio_hfp_button.set_tooltip_text("Activer explicitement le rôle mains libres hfp_hf de WirePlumber")
        self.call_audio_hfp_button.connect("clicked", self._confirm_enable_hfp_support)
        audio_actions.append(self.call_audio_hfp_button)
        audio_title = Gtk.Label(label="Micro et haut-parleurs du PC", xalign=0)
        audio_title.add_css_class("heading")
        incoming.add(audio_title)
        audio_detail = Gtk.Label(
            label="Relie le son d’appel HFP du téléphone à la sortie GNOME et le micro du PC au téléphone.",
            xalign=0,
        )
        audio_detail.set_wrap(True)
        audio_detail.add_css_class("dim-label")
        incoming.add(audio_detail)
        incoming.add(audio_actions)
        self.call_audio_status = Gtk.Label(
            label="Le téléphone doit être connecté à Debian pendant l’appel (Bluetooth vert sur Android).",
            xalign=0,
        )
        self.call_audio_status.set_wrap(True)
        self.call_audio_status.add_css_class("dim-label")
        incoming.add(self.call_audio_status)
        recording_row = Adw.ActionRow.new()
        recording_row.set_title("Enregistrement automatique")
        recording_row.set_subtitle("Enregistre les appels entrants et sortants avec le son du PC.")
        self.call_recording_switch = Gtk.Switch()
        self.call_recording_switch.set_valign(Gtk.Align.CENTER)
        self.call_recording_switch.set_active(self.call_recording_enabled)
        self.call_recording_switch.connect("notify::active", self._call_recording_toggled)
        recording_row.add_suffix(self.call_recording_switch)
        recording_row.set_activatable_widget(self.call_recording_switch)
        incoming.add(recording_row)
        self.call_recording_status = Gtk.Label(
            label=("Enregistrement automatique activé · fichiers WAV dans Musique/DroidDesk Calls."
                   if self.call_recording_enabled else "Enregistrement désactivé."),
            xalign=0,
        )
        self.call_recording_status.set_wrap(True)
        self.call_recording_status.add_css_class("dim-label")
        incoming.add(self.call_recording_status)
        page.append(incoming)

        quick_links = Gtk.FlowBox()
        quick_links.set_selection_mode(Gtk.SelectionMode.NONE)
        quick_links.set_min_children_per_line(1)
        quick_links.set_max_children_per_line(2)
        quick_links.set_row_spacing(8)
        quick_links.set_column_spacing(8)
        self.contacts_quick_button = Gtk.Button(label="Ouvrir les contacts")
        self.contacts_quick_button.connect("clicked", lambda *_: self.stack.set_visible_child_name("contacts"))
        self.ring_phone_button = Gtk.Button(label="Faire sonner le téléphone")
        self.ring_phone_button.set_tooltip_text("Faire jouer la sonnerie Android pendant 30 secondes")
        self.ring_phone_button.connect("clicked", self._ring_phone)
        self.stop_phone_ring_button = Gtk.Button(label="Arrêter la sonnerie")
        self.stop_phone_ring_button.connect("clicked", self._stop_phone_ring)
        quick_links.append(self.contacts_quick_button)
        quick_links.append(self.ring_phone_button)
        quick_links.append(self.stop_phone_ring_button)
        page.append(quick_links)

        return page

    def _ring_phone(self, *_args):
        if not self._active_device() or not self.droidlink_bridge:
            self._set_status("Connecte le téléphone pour le faire sonner.", True)
            return
        state = self.droidlink_bridge.snapshot()
        if not state["paired"] or not state.get("phone", {}).get("notificationAccess"):
            self._set_status("Relie DroidLink et active son relais en arrière-plan pour faire sonner le téléphone.", True)
            self.stack.set_visible_child_name("droidlink")
            return
        if self.droidlink_bridge.enqueue_command("ring_phone", ""):
            self._set_status("Sonnerie envoyée au téléphone; elle s’arrête automatiquement après 30 secondes.")

    def _stop_phone_ring(self, *_args):
        if self.droidlink_bridge and self.droidlink_bridge.enqueue_command("stop_phone_ring", ""):
            self._set_status("Arrêt de la sonnerie envoyé au téléphone.")

    def _build_screen_page(self):
        page = self._page()
        page_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page_header.add_css_class("page-heading")
        title = Gtk.Label(label="Écran et apps", xalign=0)
        title.add_css_class("page-title")
        title.set_hexpand(True)
        page_header.append(title)
        self.scrcpy_button = Gtk.Button(label="scrcpy")
        self.scrcpy_button.add_css_class("flat")
        self.scrcpy_button.set_tooltip_text("Afficher et contrôler l’écran dans DroidDesk")
        self.scrcpy_button.connect("clicked", self._start_scrcpy)
        page_header.append(self.scrcpy_button)
        page.append(page_header)

        self.embedded_viewer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.embedded_viewer.add_css_class("embedded-screen")
        self.embedded_viewer.set_visible(False)
        viewer_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.embedded_back_to_apps_button = Gtk.Button(label="Retour aux apps")
        self.embedded_back_to_apps_button.connect("clicked", self._close_embedded_viewer)
        viewer_toolbar.append(self.embedded_back_to_apps_button)
        self.embedded_app_title = Gtk.Label(label="Écran du téléphone", xalign=0)
        self.embedded_app_title.add_css_class("heading")
        self.embedded_app_title.set_hexpand(True)
        viewer_toolbar.append(self.embedded_app_title)
        for label, keycode, tooltip in (
            ("Retour", "KEYCODE_BACK", "Retour Android"),
            ("Accueil", "KEYCODE_HOME", "Accueil Android"),
            ("Récents", "KEYCODE_APP_SWITCH", "Applications récentes Android"),
        ):
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda _button, code=keycode: self._send_embedded_key(code))
            viewer_toolbar.append(button)
        self.embedded_viewer.append(viewer_toolbar)
        self.embedded_screen_status = Gtk.Label(label="Connexion à scrcpy…", xalign=0)
        self.embedded_screen_status.add_css_class("dim-label")
        self.embedded_viewer.append(self.embedded_screen_status)
        self.embedded_picture = Gtk.Picture.new()
        self.embedded_picture.set_can_shrink(True)
        self.embedded_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.embedded_picture.set_size_request(320, 480)
        self.embedded_picture.set_halign(Gtk.Align.CENTER)
        self.embedded_picture.set_vexpand(True)
        self.embedded_picture.set_focusable(True)
        self.embedded_picture.add_css_class("embedded-screen-picture")
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._embedded_drag_begin)
        drag.connect("drag-end", self._embedded_drag_end)
        self.embedded_picture.add_controller(drag)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._embedded_key_pressed)
        self.embedded_picture.add_controller(keys)
        self.embedded_viewer.append(self.embedded_picture)
        page.append(self.embedded_viewer)

        self.apps_group = Adw.PreferencesGroup.new()
        self.apps_group.set_title("Applications")
        app_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.apps_search = Gtk.SearchEntry()
        self.apps_search.set_hexpand(True)
        self.apps_search.set_placeholder_text("Filtrer les applications")
        self.apps_search.connect("search-changed", self._on_apps_search)
        app_controls.append(self.apps_search)
        self.apps_refresh_button = Gtk.Button(label="Actualiser")
        self.apps_refresh_button.connect("clicked", lambda *_: self._refresh_apps())
        app_controls.append(self.apps_refresh_button)
        apps_row = Adw.ActionRow.new()
        apps_row.set_title("Recherche")
        apps_row.add_suffix(app_controls)
        self.apps_group.add(apps_row)
        self.apps_icon_status = Gtk.Label(label="Icônes originales du téléphone", xalign=0)
        self.apps_icon_status.add_css_class("dim-label")
        self.apps_group.add(self.apps_icon_status)
        self.apps_grid = Gtk.FlowBox()
        self.apps_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.apps_grid.set_homogeneous(True)
        self.apps_grid.set_min_children_per_line(2)
        self.apps_grid.set_max_children_per_line(5)
        self.apps_grid.set_row_spacing(12)
        self.apps_grid.set_column_spacing(12)
        self.apps_grid.set_valign(Gtk.Align.START)
        apps_scroll = Gtk.ScrolledWindow()
        apps_scroll.set_min_content_height(0)
        apps_scroll.set_vexpand(True)
        apps_scroll.set_child(self.apps_grid)
        self.apps_group.add(apps_scroll)
        page.append(self.apps_group)

        self.install_group = Adw.PreferencesGroup.new()
        self.install_group.set_title("Installer une application")
        self.install_group.set_description("Choisissez un fichier APK pour l’installer sur le téléphone.")
        self.install_apk_button = Gtk.Button(label="Choisir un APK…")
        self.install_apk_button.connect("clicked", self._choose_apk)
        install_row = Adw.ActionRow.new()
        install_row.set_title("Fichier APK")
        install_row.add_suffix(self.install_apk_button)
        self.install_group.add(install_row)
        page.append(self.install_group)
        return page

    def _build_gallery_page(self):
        page = self._page()
        page.append(self._heading("Galerie", "Les 2 000 photos les plus récentes du téléphone."))
        gallery_group = Adw.PreferencesGroup.new()
        gallery_group.set_title("Photos récentes · maximum 2 000")
        gallery_group.set_description("Aperçus agrandis · les vignettes téléchargées restent en cache sur cet ordinateur.")
        gallery_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.photos_gallery_button = Gtk.Button(label="Actualiser")
        self.photos_gallery_button.connect("clicked", self._load_photos_gallery)
        gallery_controls.append(self.photos_gallery_button)
        self.photos_open_button = Gtk.Button(label="Ouvrir")
        self.photos_open_button.connect("clicked", self._open_selected_gallery_photo)
        gallery_controls.append(self.photos_open_button)
        self.photos_download_button = Gtk.Button(label="Télécharger…")
        self.photos_download_button.connect("clicked", self._download_selected_gallery_photo)
        gallery_controls.append(self.photos_download_button)
        self.photos_external_button = Gtk.Button(label="Ouvrir avec…")
        self.photos_external_button.connect("clicked", self._open_selected_photo_externally)
        gallery_controls.append(self.photos_external_button)
        gallery_group.add(gallery_controls)
        self.photos_status = Gtk.Label(label="Autorise l’accès aux photos dans DroidLink → Accès, puis actualise.", xalign=0)
        self.photos_status.set_wrap(True)
        self.photos_status.add_css_class("dim-label")
        gallery_group.add(self.photos_status)
        self.photos_flow = Gtk.FlowBox()
        self.photos_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.photos_flow.set_homogeneous(True)
        self.photos_flow.set_min_children_per_line(1)
        self.photos_flow.set_max_children_per_line(4)
        self.photos_flow.set_row_spacing(14)
        self.photos_flow.set_column_spacing(14)
        self.photos_flow.set_valign(Gtk.Align.START)
        self.photos_scroll = Gtk.ScrolledWindow()
        self.photos_scroll.set_min_content_height(0)
        self.photos_scroll.set_vexpand(True)
        self.photos_scroll.set_child(self.photos_flow)
        self.photos_scroll.get_vadjustment().connect("value-changed", self._gallery_scroll_changed)
        gallery_group.add(self.photos_scroll)
        page.append(gallery_group)
        return page

    def _build_files_page(self):
        page = self._page()
        page.append(self._heading("Fichiers", "Parcourez le stockage partagé du téléphone et transférez des fichiers."))

        bar = Gtk.FlowBox()
        bar.set_selection_mode(Gtk.SelectionMode.NONE)
        bar.set_min_children_per_line(1)
        bar.set_max_children_per_line(6)
        bar.set_row_spacing(6)
        bar.set_column_spacing(6)
        self.up_button = Gtk.Button.new_from_icon_name("go-up-symbolic")
        self.up_button.set_tooltip_text("Dossier parent")
        self.up_button.connect("clicked", self._go_parent)
        bar.append(self.up_button)
        self.remote_path_label = Gtk.Label(label=self.remote_dir, xalign=0)
        self.remote_path_label.set_max_width_chars(24)
        self.remote_path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.remote_path_label.add_css_class("monospace")
        bar.append(self.remote_path_label)
        self.files_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.files_refresh_button.set_tooltip_text("Actualiser")
        self.files_refresh_button.connect("clicked", lambda *_: self._load_directory())
        bar.append(self.files_refresh_button)
        self.photos_dcim_button = Gtk.Button(label="Photos · DCIM")
        self.photos_dcim_button.connect("clicked", lambda *_: self._open_photo_folder("/sdcard/DCIM"))
        bar.append(self.photos_dcim_button)
        self.photos_pictures_button = Gtk.Button(label="Images · Pictures")
        self.photos_pictures_button.connect("clicked", lambda *_: self._open_photo_folder("/sdcard/Pictures"))
        bar.append(self.photos_pictures_button)
        self.mount_phone_storage_button = Gtk.Button(label="Monter dans Nautilus · ADB Wi-Fi")
        self.mount_phone_storage_button.set_tooltip_text("Monter /sdcard dans Nautilus avec ADB sans fil et FUSE")
        self.mount_phone_storage_button.connect("clicked", self._mount_phone_storage)
        bar.append(self.mount_phone_storage_button)
        self.mkdir_button = Gtk.Button(label="Nouveau dossier")
        self.mkdir_button.connect("clicked", self._new_directory)
        bar.append(self.mkdir_button)
        page.append(bar)

        self.file_list = Gtk.ListBox()
        self.file_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.file_list.add_css_class("boxed-list")
        self.file_list.connect("row-selected", self._on_file_selected)
        self.file_list.connect("row-activated", self._on_file_activated)
        file_scroll = Gtk.ScrolledWindow()
        file_scroll.set_min_content_height(0)
        file_scroll.set_vexpand(True)
        file_scroll.set_child(self.file_list)
        page.append(file_scroll)

        transfer = Gtk.FlowBox()
        transfer.set_selection_mode(Gtk.SelectionMode.NONE)
        transfer.set_min_children_per_line(1)
        transfer.set_max_children_per_line(2)
        transfer.set_row_spacing(6)
        transfer.set_column_spacing(6)
        self.upload_button = Gtk.Button(label="Envoyer vers le téléphone…")
        self.upload_button.connect("clicked", self._choose_upload)
        self.download_button = Gtk.Button(label="Télécharger vers Debian…")
        self.download_button.connect("clicked", self._choose_download_folder)
        transfer.append(self.upload_button)
        transfer.append(self.download_button)
        page.append(transfer)

        share_link = Adw.PreferencesGroup.new()
        share_link.set_title("Envoyer un lien au téléphone")
        share_link.set_description("Ouvre une adresse Web dans le navigateur Android.")
        link_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.phone_link_entry = Gtk.Entry()
        self.phone_link_entry.set_hexpand(True)
        self.phone_link_entry.set_input_purpose(Gtk.InputPurpose.URL)
        self.phone_link_entry.set_placeholder_text("https://exemple.com")
        self.phone_link_entry.connect("activate", self._send_link_to_phone)
        link_controls.append(self.phone_link_entry)
        self.phone_link_button = Gtk.Button(label="Ouvrir sur Android")
        self.phone_link_button.connect("clicked", self._send_link_to_phone)
        link_controls.append(self.phone_link_button)
        share_link.add(link_controls)
        page.append(share_link)

        self.files_note = Gtk.Label(
            label="Nautilus monte /sdcard par ADB Wi-Fi avec madbfs et FUSE. Les dossiers privés des applications restent protégés par Android.",
            xalign=0,
        )
        self.files_note.set_wrap(True)
        self.files_note.add_css_class("dim-label")
        page.append(self.files_note)
        return page

    def update_bluetooth_view(self):
        if not hasattr(self, "bluez"):
            return
        adapters = [
            (path, interfaces["org.bluez.Adapter1"])
            for path, interfaces in self.bluez.objects.items()
            if "org.bluez.Adapter1" in interfaces
        ]
        adapters.sort(key=lambda item: item[0])
        old_path = self.selected_adapter
        self.adapter_paths = [path for path, _props in adapters]
        labels = [
            f"{props.get('Alias') or props.get('Name') or 'Adaptateur Bluetooth'} — {controller_identity(path, props)}"
            for path, props in adapters
        ]

        self._updating_bluetooth_controls = True
        self.bt_adapter_model.splice(0, self.bt_adapter_model.get_n_items(), labels or ["Aucun adaptateur"])
        if old_path in self.adapter_paths:
            selected = self.adapter_paths.index(old_path)
        else:
            selected = 0 if self.adapter_paths else Gtk.INVALID_LIST_POSITION
            self.selected_adapter = self.adapter_paths[0] if self.adapter_paths else None
        self.bt_adapter_row.set_selected(selected)
        adapter = self.bluez.props(self.selected_adapter, "org.bluez.Adapter1") if self.selected_adapter else {}
        self.bt_adapter_row.set_subtitle(controller_identity(self.selected_adapter, adapter) if self.selected_adapter else "Aucun contrôleur détecté")
        powered = bool(adapter.get("Powered", False))
        has_adapter = bool(adapter)
        self.bt_power_row.set_active(powered)
        self.bt_discoverable_row.set_active(bool(adapter.get("Discoverable", False)))
        self.bt_pairable_row.set_active(bool(adapter.get("Pairable", False)))
        self.bt_power_row.set_sensitive(has_adapter)
        self.bt_discoverable_row.set_sensitive(has_adapter and powered)
        self.bt_pairable_row.set_sensitive(has_adapter and powered)
        self.bt_scan_button.set_sensitive(has_adapter and powered)
        self.bt_scan_button.set_label("Arrêter la recherche" if adapter.get("Discovering", False) else "Rechercher des appareils")
        self.bt_refresh_button.set_sensitive(has_adapter)
        self._updating_bluetooth_controls = False

        devices = []
        query = self.bt_search.get_text().strip().casefold()
        if self.selected_adapter:
            prefix = self.selected_adapter.rstrip("/") + "/"
            for path, interfaces in self.bluez.objects.items():
                props = interfaces.get("org.bluez.Device1")
                if not props or not path.startswith(prefix):
                    continue
                name = str(props.get("Alias") or props.get("Name") or "").casefold()
                address = str(props.get("Address") or "").casefold()
                if query and query not in name and query not in address:
                    continue
                devices.append((path, props))
        devices.sort(key=lambda item: (not item[1].get("Connected", False), not item[1].get("Paired", False), str(item[1].get("Alias") or item[1].get("Name") or "").casefold()))
        while child := self.bt_device_list.get_first_child():
            self.bt_device_list.remove(child)
        if not has_adapter:
            self._add_bt_empty("Aucun contrôleur Bluetooth détecté", "Branchez ou activez un adaptateur Bluetooth.")
        elif not powered:
            self._add_bt_empty("Bluetooth désactivé", "Activez le contrôleur sélectionné ci-dessus.")
        elif not devices:
            self._add_bt_empty("Aucun appareil trouvé", "Lancez une recherche pour détecter les appareils à proximité.")
        else:
            sections = (
                ("Connectés", [item for item in devices if item[1].get("Connected", False)]),
                ("Enregistrés", [item for item in devices if item[1].get("Paired", False) and not item[1].get("Connected", False)]),
                ("À proximité", [item for item in devices if not item[1].get("Paired", False)]),
            )
            for title, entries in sections:
                if not entries:
                    continue
                heading = Gtk.ListBoxRow()
                heading.set_selectable(False)
                heading.set_activatable(False)
                label = Gtk.Label(label=f"{title}  ·  {len(entries)}", xalign=0)
                label.add_css_class("heading")
                label.set_margin_top(12)
                label.set_margin_bottom(5)
                heading.set_child(label)
                self.bt_device_list.append(heading)
                for path, props in entries:
                    self.bt_device_list.append(self._bt_device_row(path, props))
        self.bt_hint.set_text("Appels sur le PC : connecte le téléphone à Debian comme casque mains libres HFP/HSP. Si Android affiche « non valide », oublie Debian dans les deux réglages Bluetooth, relance l’appairage, puis vérifie que le profil Appels est activé pour Debian.")

    def _add_bt_empty(self, title, detail):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(26)
        box.set_margin_bottom(26)
        icon = Gtk.Image.new_from_icon_name("bluetooth-symbolic")
        icon.set_pixel_size(40)
        icon.add_css_class("dim-label")
        label = Gtk.Label(label=title)
        label.add_css_class("title-3")
        description = Gtk.Label(label=detail)
        description.set_wrap(True)
        description.set_max_width_chars(52)
        description.set_justify(Gtk.Justification.CENTER)
        description.add_css_class("dim-label")
        box.append(icon)
        box.append(label)
        box.append(description)
        row.set_child(box)
        self.bt_device_list.append(row)

    @staticmethod
    def _bt_device_icon(props):
        icon = str(props.get("Icon", ""))
        if icon in ("audio-headset", "audio-card", "audio-input-microphone"):
            return "audio-headphones-symbolic"
        if icon in ("input-keyboard", "input-mouse", "input-gaming"):
            return "input-gaming-symbolic"
        if icon in ("phone", "smartphone"):
            return "phone-symbolic"
        if icon in ("computer", "video-display"):
            return "computer-symbolic"
        return "bluetooth-symbolic"

    def _bt_device_row(self, path, props):
        name = props.get("Alias") or props.get("Name") or "Appareil sans nom"
        states = [label for key, label in (("Connected", "Connecté"), ("Paired", "Enregistré"), ("Trusted", "De confiance"), ("Blocked", "Bloqué")) if props.get(key, False)]
        address = str(props.get("Address") or "")
        subtitle = " · ".join(states) if states else "À proximité"
        if address:
            subtitle += f"  •  {address.upper()}"
        row = Adw.ActionRow.new()
        row.set_title(GLib.markup_escape_text(name))
        row.set_subtitle(subtitle)
        row.add_prefix(Gtk.Image.new_from_icon_name(self._bt_device_icon(props)))
        action = Gtk.Button()
        action.add_css_class("flat")
        action.set_valign(Gtk.Align.CENTER)
        action.set_sensitive(path not in self.bluetooth_busy)
        if props.get("Connected", False):
            action.set_label("Déconnecter")
            action.connect("clicked", lambda *_args, p=path: self._bt_device_action(p, "Disconnect", "Déconnexion demandée"))
        elif props.get("Paired", False):
            action.set_label("Connecter")
            action.connect("clicked", lambda *_args, p=path: self._bt_device_action(p, "Connect", "Connexion demandée"))
        else:
            action.set_label("Appairer")
            action.add_css_class("suggested-action")
            action.connect("clicked", lambda *_args, p=path: self._bt_device_action(p, "Pair", "Appairage démarré", 60_000))
        row.add_suffix(action)
        menu = Gtk.MenuButton.new()
        menu.set_icon_name("view-more-symbolic")
        menu.set_valign(Gtk.Align.CENTER)
        menu.set_popover(self._bt_device_popover(path, props))
        row.add_suffix(menu)
        return row

    def _bt_device_popover(self, path, props):
        popover = Gtk.Popover.new()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        options = [
            ("Détails et renommer", lambda: self._show_bt_details(path)),
            ("Ne plus faire confiance" if props.get("Trusted", False) else "Faire confiance", lambda: self._set_bt_device_property(path, "Trusted", not props.get("Trusted", False))),
            ("Débloquer" if props.get("Blocked", False) else "Bloquer", lambda: self._set_bt_device_property(path, "Blocked", not props.get("Blocked", False))),
            ("Oublier l’appareil", lambda: self._confirm_bt_forget(path)),
        ]
        uuids = {str(uuid).casefold() for uuid in props.get("UUIDs", []) or []}
        if props.get("Paired") and HFP_AUDIO_GATEWAY_UUID in uuids:
            options.insert(1, ("Connecter pour les appels · HFP", lambda: self._bt_connect_profile(path, HFP_AUDIO_GATEWAY_UUID)))
        for title, callback in options:
            button = Gtk.Button(label=title)
            button.add_css_class("flat")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, cb=callback: (popover.popdown(), cb()))
            box.append(button)
        popover.set_child(box)
        return popover

    def _bt_connect_profile(self, path, uuid):
        if path in self.bluetooth_busy:
            return
        self.bluetooth_busy.add(path)
        self.update_bluetooth_view()
        self.bluez.call(
            path,
            "org.bluez.Device1",
            "ConnectProfile",
            GLib.Variant("(s)", (uuid,)),
            callback=lambda _reply, error: self._bt_action_done(path, "Connexion du profil mains libres demandée", error),
            timeout=60_000,
        )

    def _bt_device_action(self, path, method, success, timeout=15_000):
        if path in self.bluetooth_busy:
            return
        self.bluetooth_busy.add(path)
        self.update_bluetooth_view()
        self.bluez.call(path, "org.bluez.Device1", method, callback=lambda _reply, error: self._bt_action_done(path, success, error), timeout=timeout)

    def _bt_action_done(self, path, success, error):
        self.bluetooth_busy.discard(path)
        detail = _error_text(error) if error else ""
        if detail and any(term in detail.casefold() for term in ("invalid", "invalide", "invalidarguments")):
            detail = f"Le profil Bluetooth demandé a été refusé. Vérifie que le profil Appels est activé pour Debian sur Android. Détail BlueZ : {detail}"
        self._set_status(f"{success} : {detail}" if error else success, bool(error))
        self.bluez.refresh()

    def _set_bt_device_property(self, path, name, value):
        self.bluez.set_property(path, "org.bluez.Device1", name, "b", bool(value), lambda _reply, error: self._set_status(f"Mise à jour impossible : {_error_text(error)}" if error else "Appareil Bluetooth mis à jour", bool(error)))

    def _confirm_bt_forget(self, path):
        props = self.bluez.props(path, "org.bluez.Device1")
        name = props.get("Alias") or props.get("Name") or "cet appareil"
        dialog = Gtk.Dialog(title="Oublier l’appareil ?", transient_for=self, modal=True)
        content = dialog.get_content_area()
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(18)
        content.set_margin_end(18)
        label = Gtk.Label(label=f"L’appairage avec {name} sera supprimé.", xalign=0)
        label.set_wrap(True)
        content.append(label)
        dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
        dialog.add_button("Oublier", Gtk.ResponseType.ACCEPT)
        dialog.connect("response", lambda current, answer: self._forget_bt_response(current, answer, path))
        dialog.present()

    def _forget_bt_response(self, dialog, answer, path):
        dialog.destroy()
        if answer != Gtk.ResponseType.ACCEPT or not self.selected_adapter:
            return
        self.bluez.call(self.selected_adapter, "org.bluez.Adapter1", "RemoveDevice", GLib.Variant("(o)", (path,)), callback=lambda _reply, error: self._set_status(f"Suppression impossible : {_error_text(error)}" if error else "Appareil Bluetooth oublié", bool(error)))

    def _show_bt_details(self, path):
        props = self.bluez.props(path, "org.bluez.Device1")
        dialog = Gtk.Dialog(title=props.get("Alias") or props.get("Name") or "Appareil Bluetooth", transient_for=self, modal=True)
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(18)
        content.set_margin_end(18)
        alias = Gtk.Entry()
        alias.set_text(props.get("Alias") or props.get("Name") or "")
        alias.set_placeholder_text("Nom de l’appareil")
        content.append(alias)
        address = Gtk.Label(label=f"Adresse : {str(props.get('Address', 'inconnue')).upper()}", xalign=0)
        address.set_selectable(True)
        content.append(address)
        uuids = props.get("UUIDs", []) or []
        services = Gtk.Label(label="Services Bluetooth\n" + ("\n".join(uuids) if uuids else "Disponibles après connexion."), xalign=0)
        services.set_selectable(True)
        services.set_wrap(True)
        content.append(services)
        dialog.add_button("Fermer", Gtk.ResponseType.CANCEL)
        dialog.add_button("Enregistrer le nom", Gtk.ResponseType.ACCEPT)

        def response(current, answer):
            current.destroy()
            new_name = alias.get_text().strip()
            if answer == Gtk.ResponseType.ACCEPT and new_name and new_name != props.get("Alias"):
                self.bluez.set_property(path, "org.bluez.Device1", "Alias", "s", new_name)

        dialog.connect("response", response)
        dialog.present()

    def _agent_prompt(self, invocation, kind, device_name, title, result_type, body, pending):
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(18)
        content.set_margin_end(18)
        message = f"{body}\nAppareil : {device_name}" if body else f"Demande Bluetooth provenant de {device_name}."
        label = Gtk.Label(label=message, xalign=0)
        label.set_wrap(True)
        content.append(label)
        entry = None
        if kind in ("pin", "passkey"):
            entry = Gtk.Entry()
            entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            entry.set_placeholder_text("Code PIN" if kind == "pin" else "Code à 6 chiffres")
            content.append(entry)
        dialog.add_button("Refuser", Gtk.ResponseType.CANCEL)
        dialog.add_button("Confirmer", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        key = id(invocation)
        pending[key] = dialog

        def response(current, answer):
            pending.pop(key, None)
            current.destroy()
            if answer != Gtk.ResponseType.ACCEPT:
                invocation.return_dbus_error("org.bluez.Error.Rejected", "Demande refusée")
            elif kind == "pin":
                code = entry.get_text().strip()
                if not code:
                    invocation.return_dbus_error("org.bluez.Error.Rejected", "Code PIN vide")
                else:
                    invocation.return_value(GLib.Variant(result_type, (code,)))
            elif kind == "passkey":
                code = entry.get_text().strip()
                if not code.isdigit() or len(code) > 6:
                    invocation.return_dbus_error("org.bluez.Error.Rejected", "Code invalide")
                else:
                    invocation.return_value(GLib.Variant(result_type, (int(code),)))
            else:
                invocation.return_value(GLib.Variant(result_type, ()))

        dialog.connect("response", response)
        dialog.present()

    def _on_bt_adapter_selected(self, row, _param):
        if self._updating_bluetooth_controls:
            return
        index = row.get_selected()
        self.selected_adapter = self.adapter_paths[index] if index < len(self.adapter_paths) else None
        self.update_bluetooth_view()

    def _on_bt_power(self, row, _param):
        if not self._updating_bluetooth_controls:
            self._set_bt_adapter_property("Powered", row.get_active())

    def _on_bt_discoverable(self, row, _param):
        if not self._updating_bluetooth_controls:
            self._set_bt_adapter_property("Discoverable", row.get_active())

    def _on_bt_pairable(self, row, _param):
        if not self._updating_bluetooth_controls:
            self._set_bt_adapter_property("Pairable", row.get_active())

    def _set_bt_adapter_property(self, name, value):
        if not self.selected_adapter:
            return
        self.bluez.set_property(self.selected_adapter, "org.bluez.Adapter1", name, "b", bool(value), lambda _reply, error: self._set_status(f"Réglage Bluetooth impossible : {_error_text(error)}" if error else "Réglage Bluetooth mis à jour", bool(error)))

    def _toggle_bt_scan(self, *_args):
        if not self.selected_adapter:
            return
        adapter = self.bluez.props(self.selected_adapter, "org.bluez.Adapter1")
        method = "StopDiscovery" if adapter.get("Discovering", False) else "StartDiscovery"
        self.bluez.call(self.selected_adapter, "org.bluez.Adapter1", method, callback=lambda _reply, error: self._set_status(f"Recherche Bluetooth impossible : {_error_text(error)}" if error else ("Recherche arrêtée" if method == "StopDiscovery" else "Recherche Bluetooth démarrée"), bool(error)))

    def _on_close(self, *_args):
        for serial in list(getattr(self, "phone_mounts", {})):
            self._unmount_phone_storage(serial)
        recording = self.call_recording_process
        self.call_recording_process = None
        if recording:
            self._finish_call_recording_process(recording)
        pactl = shutil.which("pactl")
        if pactl:
            for module_id in self.call_audio_module_ids:
                subprocess.run(
                    [pactl, "unload-module", str(module_id)],
                    capture_output=True, timeout=3, check=False,
                )
            if self.call_audio_original_profile:
                card_name, profile_name = self.call_audio_original_profile
                if profile_name:
                    subprocess.run(
                        [pactl, "set-card-profile", card_name, profile_name],
                        capture_output=True, timeout=3, check=False,
                    )
        if hasattr(self, "bluez"):
            self.bluez.close()
        if getattr(self, "droidlink_bridge", None):
            self.droidlink_bridge.close()
        if getattr(self, "droidlink_timer", 0):
            GLib.source_remove(self.droidlink_timer)
            self.droidlink_timer = 0
        if self.photo_preview_dir:
            self.photo_preview_dir.cleanup()
            self.photo_preview_dir = None
        for cache in self.external_photo_dirs:
            cache.cleanup()
        self.external_photo_dirs.clear()
        return False

    def _start_droidlink_pairing(self, *_args):
        if not self.droidlink_bridge:
            self._set_status("Le serveur local DroidLink n’a pas pu démarrer.", True)
            return
        active = self._active_device()
        if not active:
            self._set_status("Connectez et sélectionnez le téléphone Android avant l’appairage.", True)
            return
        code = self.droidlink_bridge.begin_pairing()
        self.droidlink_code_label.set_text(f"{code[:3]} {code[3:]}")
        self._run_adb_command(
            ["reverse", f"tcp:{PORT}", f"tcp:{PORT}"],
            "Ouverture du tunnel ADB local pour DroidLink…",
            self._droidlink_reverse_result,
        )

    def _droidlink_reverse_result(self, result, error):
        if error or not result or result.returncode != 0:
            if self.droidlink_bridge:
                self.droidlink_bridge.cancel_pairing()
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self.droidlink_code_label.set_text("—")
            self._set_status(f"Impossible d’ouvrir le tunnel ADB DroidLink : {detail}", True)
            return
        active = self._active_device()
        self._droidlink_tunnel_serial = active[0] if active else None
        self._set_status("Tunnel ADB prêt. Ouvrez DroidLink sur Android et saisissez le code affiché.")
        self._droidlink_state_changed()

    def _ensure_droidlink_tunnel(self):
        active = self._active_device()
        if not active or not self.droidlink_bridge:
            return
        if not self.droidlink_bridge.snapshot()["paired"]:
            return
        serial = active[0]
        if serial == self._droidlink_tunnel_serial:
            return
        self._run_adb_command(
            ["reverse", f"tcp:{PORT}", f"tcp:{PORT}"],
                "Rétablissement automatique du lien DroidLink…",
            lambda result, error, device=serial: self._restored_droidlink_tunnel(device, result, error),
        )

    def _restored_droidlink_tunnel(self, serial, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Le lien DroidLink n’a pas pu être rétabli : {detail}", True)
            return
        if self._active_device() and self._active_device()[0] == serial:
            self._droidlink_tunnel_serial = serial
            self._set_status("Lien DroidLink prêt pour les notifications et les fonctions du compagnon.")

    def _disconnect_droidlink(self, *_args):
        self._droidlink_tunnel_serial = None
        if self.droidlink_bridge:
            self.droidlink_bridge.disconnect()
        self.droidlink_code_label.set_text("—")
        if self._active_device():
            self._run_adb_command(
                ["reverse", "--remove", f"tcp:{PORT}"],
                "Dissociation de DroidLink et retrait du tunnel ADB…",
                self._droidlink_reverse_removed,
            )
        else:
            self._set_status("DroidLink dissocié. Le tunnel sera retiré à la prochaine connexion ADB.")

    def _droidlink_reverse_removed(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"DroidLink est dissocié; retrait du tunnel ADB impossible : {detail}", True)
        else:
            self._set_status("DroidLink dissocié et tunnel ADB retiré.")

    def _droidlink_timer_tick(self):
        if not getattr(self, "droidlink_bridge", None):
            return GLib.SOURCE_REMOVE
        self._droidlink_state_changed()
        return GLib.SOURCE_CONTINUE

    def _droidlink_state_changed(self):
        if not getattr(self, "droidlink_bridge", None):
            return GLib.SOURCE_REMOVE
        state = self.droidlink_bridge.snapshot()
        if state["paired"]:
            phone = state["phone"]
            name = " ".join(part for part in (phone.get("manufacturer"), phone.get("model")) if part)
            relay_ready = phone.get("notificationAccess")
            dialer = " · relais de notifications actif" if relay_ready else ""
            needs_access = " · active les notifications dans DroidLink" if not phone.get("notificationAccess") else ""
            self.droidlink_status.set_text(f"Associé · {name or 'Téléphone Android'} · Android {phone.get('android') or '—'}{dialer}{needs_access}")
            self.droidlink_code_label.set_text("—")
            battery = phone.get("batteryPercent", -1)
            self.phone_battery_label.set_text(f"Batterie · {battery}%" if isinstance(battery, int) and 0 <= battery <= 100 else "")
            self.phone_battery_label.set_visible(isinstance(battery, int) and 0 <= battery <= 100)
        elif state["code"]:
            self.droidlink_status.set_text(f"En attente du téléphone · code valide encore {state['seconds']} s.")
            code = state["code"]
            self.droidlink_code_label.set_text(f"{code[:3]} {code[3:]}")
            self.phone_battery_label.set_visible(False)
        else:
            self.droidlink_status.set_text("Aucun téléphone DroidLink associé.")
            self.droidlink_code_label.set_text("—")
            self.phone_battery_label.set_visible(False)
        self.droidlink_empty.set_visible(not self.droidlink_messages)
        self._update_controls()
        return GLib.SOURCE_REMOVE

    def _droidlink_received(self, event):
        if event.get("type") == "app_icons":
            for item in event.get("items", []):
                package = item.get("package", "")
                encoded = item.get("png", "")
                if not package or not encoded:
                    continue
                try:
                    data = base64.b64decode(encoded, validate=True)
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(data)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    if pixbuf:
                        self.app_icons[package] = pixbuf.copy()
                        label = item.get("label", "")[:100]
                        self.app_labels[package] = label
                        self._write_cached_bytes(self.app_icon_cache_dir / f"{package}.png", data)
                        if label:
                            self._write_cached_bytes(self.app_icon_cache_dir / f"{package}.label", label.encode("utf-8"))
                except (ValueError, GLib.Error):
                    continue
            self._render_apps()
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_list":
            self._populate_photos_gallery(event.get("items", []))
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_thumbnail":
            self._display_photo_thumbnail(event)
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_error":
            self.gallery_loaded = True
            message = event.get("message") or "DroidLink n’a pas pu lire les photos du téléphone."
            self.photos_status.set_text(message)
            self._set_status(message, True)
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_thumbnail_error":
            return GLib.SOURCE_REMOVE
        key = event.get("key", "")
        if event.get("removed"):
            application = self.get_application()
            if application:
                application.close_android_notification(key)
                application.withdraw_notification(f"android-{key}")
            for item in self.droidlink_messages:
                if item.get("key") == key:
                    item["active"] = False
                    item["phoneRemoved"] = True
            if key and key == self.incoming_call_notification_key:
                self.incoming_call_notification_key = None
            if key and key == self.active_call_notification_key:
                self.active_call_notification_key = None
                self._finish_call_session()
            self.droidlink_messages = self.droidlink_messages[:50]
            self._save_droidlink_pins()
            self._render_droidlink_messages()
            self._update_controls()
            return GLib.SOURCE_REMOVE
        if event.get("category") == "call":
            if event.get("isIncomingCall") and key:
                self.incoming_call_notification_key = key
                self.active_call_notification_key = key
            elif key and key == self.incoming_call_notification_key:
                self.incoming_call_notification_key = None
                if event.get("ongoing"):
                    self.call_direction = "incoming"
                    self.active_call_notification_key = key
                    if not self.call_session_active:
                        self.call_session_active = True
                        self._schedule_call_audio_bridge()
            elif event.get("ongoing") and key:
                self.active_call_notification_key = key
                if not self.call_session_active:
                    self.call_session_active = True
                    self._schedule_call_audio_bridge()
            self._update_controls()
        event["pinned"] = event.get("key", "") in self.droidlink_pinned
        event["active"] = True
        self._send_gnome_notification(event)
        existing = next((index for index, item in enumerate(self.droidlink_messages) if key and item.get("key") == key), None)
        if existing is None:
            self.droidlink_messages.insert(0, event)
        else:
            self.droidlink_messages[existing] = event
        pinned = [item for item in self.droidlink_messages if item.get("key") in self.droidlink_pinned]
        recent = [item for item in self.droidlink_messages if item.get("key") not in self.droidlink_pinned]
        self.droidlink_messages = pinned[:50] + recent[:max(0, 50 - len(pinned))]
        self._save_droidlink_pins()
        self._render_droidlink_messages()
        self._set_status("Nouvelle notification reçue par DroidLink.")
        return GLib.SOURCE_REMOVE

    def _send_gnome_notification(self, event):
        application = self.get_application()
        if not application:
            return
        application.send_android_notification(event)

    def _send_gnome_notification_fallback(self, event):
        application = self.get_application()
        if not application:
            return
        title = event.get("title") or event.get("app") or "Notification Android"
        body = event.get("text", "")
        messages = event.get("messages", [])
        if messages:
            latest = messages[-1]
            snippet = latest.get("text", "")
            sender = latest.get("sender", "")
            if snippet:
                body = f"{sender}: {snippet}" if sender else snippet
        notification = Gio.Notification.new(title[:180])
        if body:
            notification.set_body(body[:800])
        notification.set_icon(Gio.ThemedIcon.new("phone-symbolic"))
        key = event.get("key", "")
        if event.get("isIncomingCall"):
            notification.set_title(f"Appel entrant · {title[:140]}")
            notification.add_button("Répondre", "app.answer-phone-call")
            notification.add_button("Refuser", "app.reject-phone-call")
        if key:
            notification.set_default_action_and_target(
                "app.open-android-notification", GLib.Variant("s", key)
            )
            reply_action = next(
                (action for action in event.get("actions", []) if action.get("remoteInputKey")),
                None,
            )
            if reply_action:
                target = json.dumps(
                    {
                        "key": key,
                        "index": reply_action.get("index", -1),
                        "remoteInputKey": reply_action.get("remoteInputKey", ""),
                        "title": reply_action.get("title") or "Répondre",
                    },
                    ensure_ascii=False,
                )
                notification.add_button_with_target(
                    "Répondre", "app.reply-android-notification", GLib.Variant("s", target)
                )
        application.send_notification(f"android-{key or title[:80]}", notification)

    def _render_droidlink_messages(self):
        child = self.droidlink_list.get_first_child()
        while child:
            self.droidlink_list.remove(child)
            child = self.droidlink_list.get_first_child()
        self.droidlink_messages.sort(key=lambda event: event.get("key", "") in self.droidlink_pinned, reverse=True)
        for event in self.droidlink_messages:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.add_css_class("card")
            row.set_margin_start(6)
            row.set_margin_end(6)
            row.set_margin_top(4)
            row.set_margin_bottom(4)
            title = event.get("title") or event.get("app") or "Notification Android"
            title_label = Gtk.Label(label=title, xalign=0)
            title_label.add_css_class("heading")
            row.append(title_label)
            body = event.get("text", "")
            if body:
                body_label = Gtk.Label(label=body, xalign=0)
                body_label.set_wrap(True)
                row.append(body_label)
            posted = event.get("postedAt", 0)
            try:
                when = datetime.fromtimestamp(posted / 1000).strftime("%H:%M") if posted else ""
            except (OSError, OverflowError, ValueError):
                when = ""
            metadata = " · ".join(part for part in (event.get("app", ""), when) if part)
            if metadata:
                metadata_label = Gtk.Label(label=metadata, xalign=0)
                metadata_label.add_css_class("dim-label")
                row.append(metadata_label)
            key = event.get("key", "")
            if key and event.get("active", True):
                for phone_action in event.get("actions", []):
                    action_title = phone_action.get("title") or "Action Android"
                    action_index = phone_action.get("index", -1)
                    remote_key = phone_action.get("remoteInputKey", "")
                    if action_index < 0:
                        continue
                    if remote_key:
                        reply_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        reply_entry = Gtk.Entry()
                        reply_entry.set_hexpand(True)
                        reply_entry.set_placeholder_text(f"Répondre · {action_title}")
                        reply_row.append(reply_entry)
                        reply_button = Gtk.Button(label=action_title)
                        reply_button.add_css_class("suggested-action")
                        reply_button.connect(
                            "clicked",
                            lambda _button, entry=reply_entry, notification_key=key, index=action_index, result_key=remote_key, title=action_title:
                            self._send_droidlink_notification_action(notification_key, index, result_key, entry.get_text(), title),
                        )
                        reply_row.append(reply_button)
                        row.append(reply_row)
                    else:
                        phone_button = Gtk.Button(label=action_title)
                        phone_button.connect(
                            "clicked",
                            lambda *_args, notification_key=key, index=action_index, title=action_title:
                            self._send_droidlink_notification_action(notification_key, index, action_title=title),
                        )
                        row.append(phone_button)
                actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                open_button = Gtk.Button.new_from_icon_name("document-open-symbolic")
                open_button.set_tooltip_text("Ouvrir sur le téléphone")
                open_button.connect("clicked", lambda *_args, notification_key=key: self._send_droidlink_command("open", notification_key))
                actions.append(open_button)
                pinned = key in self.droidlink_pinned
                pin_button = Gtk.Button(label="Épinglée" if pinned else "Épingler")
                pin_button.set_tooltip_text("Conserver cette notification en haut de la liste")
                pin_button.connect("clicked", lambda *_args, notification_key=key: self._toggle_droidlink_pin(notification_key))
                actions.append(pin_button)
                dismiss_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
                dismiss_button.set_tooltip_text("Ignorer sur le téléphone")
                dismiss_button.connect("clicked", lambda *_args, notification_key=key: self._send_droidlink_command("dismiss", notification_key))
                actions.append(dismiss_button)
                row.append(actions)
            elif key:
                archived = Gtk.Label(label="Retirée du téléphone · conservée dans l’historique", xalign=0)
                archived.add_css_class("dim-label")
                row.append(archived)
            self.droidlink_list.append(row)
        self.droidlink_empty.set_visible(not self.droidlink_messages)
        self.droidlink_clear_button.set_sensitive(bool(self.droidlink_messages))

    def _send_droidlink_command(self, action, key):
        if not self.droidlink_bridge or not self.droidlink_bridge.enqueue_command(action, key):
            self._set_status("Associez DroidLink avant de contrôler les notifications.", True)
            return
        if action == "dismiss":
            application = self.get_application()
            if application:
                application.close_android_notification(key)
            for item in self.droidlink_messages:
                if item.get("key") == key:
                    item["active"] = False
            self._save_droidlink_pins()
            self._render_droidlink_messages()
            self._set_status("La notification sera ignorée sur le téléphone au prochain échange ADB.")
        else:
            self._set_status("Ouverture de la notification demandée sur Android.")

    def _clear_droidlink_history(self, *_args):
        active_keys = [
            item.get("key", "")
            for item in self.droidlink_messages
            if item.get("key") and item.get("active", True)
        ]
        for key in active_keys:
            if self.droidlink_bridge:
                self.droidlink_bridge.enqueue_command("dismiss", key)
            application = self.get_application()
            if application:
                application.close_android_notification(key)
        self.droidlink_messages.clear()
        self.droidlink_pinned.clear()
        self._save_droidlink_pins()
        self._render_droidlink_messages()
        self._set_status("Historique effacé; les alertes actives seront aussi retirées du téléphone.")

    def _send_droidlink_notification_action(self, key, index, remote_input_key="", text="", action_title=""):
        if remote_input_key and not text.strip():
            self._set_status("Écris une réponse avant de l’envoyer.", True)
            return
        if not self.droidlink_bridge or not self.droidlink_bridge.enqueue_command(
            "notification_action",
            key,
            actionIndex=index,
            remoteInputKey=remote_input_key,
            text=text,
            actionTitle=action_title,
        ):
            self._set_status("Associez DroidLink avant d’utiliser les actions de cette notification.", True)
            return
        self._set_status("Action envoyée au téléphone via DroidLink.")

    def _toggle_droidlink_pin(self, key):
        if key in self.droidlink_pinned:
            self.droidlink_pinned.remove(key)
        else:
            self.droidlink_pinned.add(key)
        for event in self.droidlink_messages:
            if event.get("key") == key:
                event["pinned"] = key in self.droidlink_pinned
        self._save_droidlink_pins()
        self._render_droidlink_messages()

    @staticmethod
    def _droidlink_pins_path():
        return Path(GLib.get_user_data_dir()) / APP_ID / "droidlink-pins.json"

    def _load_droidlink_pins(self):
        try:
            values = json.loads(self._droidlink_pins_path().read_text(encoding="utf-8"))
            if isinstance(values, dict):
                messages = [
                    item for item in values.get("messages", [])
                    if isinstance(item, dict) and item.get("key")
                ][:50]
                if "pinned" in values:
                    pinned = {
                        str(key) for key in values.get("pinned", [])
                        if isinstance(key, str)
                    }
                else:
                    # Older builds stored only pinned notifications in this file.
                    pinned = {item["key"] for item in messages}
                for item in messages:
                    item.setdefault("active", True)
                return pinned, messages
        except (OSError, ValueError, TypeError):
            pass
        return set(), []

    def _save_droidlink_pins(self):
        path = self._droidlink_pins_path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            messages = self.droidlink_messages[:50]
            path.write_text(
                json.dumps({"messages": messages, "pinned": sorted(self.droidlink_pinned)}, ensure_ascii=False),
                encoding="utf-8",
            )
            path.chmod(0o600)
        except OSError as error:
            self._set_status(f"Impossible d’enregistrer les notifications épinglées : {error}", True)

    def _ask_adb_pair(self, *_args):
        self._show_adb_connection_dialog(pair=True)

    def _ask_adb_connect(self, *_args):
        self._show_adb_connection_dialog(pair=False)

    def _show_adb_connection_dialog(self, pair):
        title = "Appairer ADB sans fil" if pair else "Connecter ADB sans fil"
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.set_default_size(440, -1)
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(18)
        content.set_margin_bottom(14)
        content.set_margin_start(18)
        content.set_margin_end(18)
        address = Gtk.Entry()
        address.set_placeholder_text("Adresse et port, par ex. 192.168.1.25:37123")
        content.append(address)
        code = None
        if pair:
            code = Gtk.Entry()
            code.set_input_purpose(Gtk.InputPurpose.DIGITS)
            code.set_placeholder_text("Code d’appairage Android")
            content.append(code)
            note = Gtk.Label(label="Sur le téléphone : Options pour les développeurs → Débogage sans fil → Appairer avec un code.", xalign=0)
            note.set_wrap(True)
            note.add_css_class("dim-label")
            content.append(note)
        dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
        dialog.add_button("Connecter" if not pair else "Appairer", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)

        def response(current, answer):
            current.destroy()
            if answer != Gtk.ResponseType.ACCEPT:
                return
            endpoint = address.get_text().strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", endpoint) or ":" not in endpoint:
                self._set_status("Entrez une adresse IP suivie du port, par exemple 192.168.1.25:37123.", True)
                return
            if pair:
                pairing_code = code.get_text().strip()
                if not pairing_code or not pairing_code.isdigit():
                    self._set_status("Entrez le code d’appairage affiché sur Android.", True)
                    return
                args = ["pair", endpoint, pairing_code]
                message = "Appairage ADB sans fil…"
            else:
                args = ["connect", endpoint]
                message = "Connexion ADB sans fil…"
            self._run_adb_command(args, message, self._adb_wireless_result, needs_device=False, timeout=30)

        dialog.connect("response", response)
        dialog.present()

    def _adb_wireless_result(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Connexion ADB sans fil échouée : {detail}", True)
            return
        self._set_status((result.stdout or "Commande ADB sans fil terminée.").strip())
        self.refresh_devices()

    def _show_adb_output(self, text):
        self.adb_output_buffer.set_text(text or "(aucune sortie)")
        end = self.adb_output_buffer.get_end_iter()
        self.adb_output.scroll_to_iter(end, 0.0, False, 0.0, 1.0)

    def _command_output_result(self, result, error):
        if error or not result:
            self._show_adb_output(str(error or "ADB n’a retourné aucun résultat."))
            self._set_status(f"Commande ADB échouée : {error or 'aucun résultat'}", True)
        else:
            output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
            self._show_adb_output(output)
            self._command_status(result, None, "Commande terminée; résultat affiché dans la console.")

    def _send_phone_key(self, key):
        self._run_adb_command(
            ["shell", "input", "keyevent", key],
            f"Commande {key} envoyée…",
            lambda result, error: self._command_status(result, error, "Commande envoyée au téléphone."),
        )

    def _run_shell_console(self, *_args):
        command = self.adb_shell_entry.get_text().strip()
        if not command:
            self._set_status("Entre une commande shell Android.", True)
            return
        self.adb_shell_entry.set_text("")
        self._run_adb_command(["shell", command], f"Exécution de « {command[:70]} »…", self._command_output_result, timeout=60)

    def _confirm_device_action(self, action):
        titles = {
            "system": ("Redémarrer le téléphone ?", ["reboot"], "Le téléphone va redémarrer."),
            "recovery": ("Redémarrer en récupération ?", ["reboot", "recovery"], "Le téléphone va démarrer dans le mode recovery."),
            "bootloader": ("Ouvrir le bootloader ?", ["reboot", "bootloader"], "Le téléphone va redémarrer dans le bootloader/fastboot."),
            "poweroff": ("Éteindre le téléphone ?", ["shell", "reboot", "-p"], "Le téléphone va s’éteindre."),
        }
        title, command, explanation = titles[action]
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        content = dialog.get_content_area()
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(18)
        content.set_margin_end(18)
        message = Gtk.Label(label=explanation, xalign=0)
        message.set_wrap(True)
        content.append(message)
        dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
        dialog.add_button("Continuer", Gtk.ResponseType.ACCEPT)

        def response(current, answer):
            current.destroy()
            if answer == Gtk.ResponseType.ACCEPT:
                self._run_adb_command(command, f"Commande {action} envoyée…", self._command_output_result)

        dialog.connect("response", response)
        dialog.present()

    def _save_screenshot(self, *_args):
        chooser = Gtk.FileChooserNative.new("Enregistrer une capture Android", self, Gtk.FileChooserAction.SAVE, "Enregistrer", "Annuler")
        chooser.set_current_name("droiddesk-capture.png")

        def response(dialog, answer):
            file = dialog.get_file()
            path = file.get_path() if file else None
            dialog.destroy()
            if answer == Gtk.ResponseType.ACCEPT and path:
                self._capture_to_file(path)

        chooser.connect("response", response)
        chooser.show()

    def _capture_to_file(self, path):
        active = self._active_device()
        if not active or self.busy:
            return
        self._set_busy(True)
        self._set_status("Capture de l’écran Android…")

        def worker():
            try:
                result = subprocess.run([self.adb, "-s", active[0], "exec-out", "screencap", "-p"], capture_output=True, timeout=15, check=False)
                if result.returncode != 0 or not result.stdout:
                    raise OSError(result.stderr.decode("utf-8", "replace").strip() or "Capture impossible")
                Path(path).write_bytes(result.stdout)
                error = None
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
            GLib.idle_add(self._screenshot_saved, path, error)

        threading.Thread(target=worker, daemon=True).start()

    def _screenshot_saved(self, path, error):
        self._set_busy(False)
        self._set_status(f"Capture enregistrée : {path}" if not error else f"Capture impossible : {error}", bool(error))
        return GLib.SOURCE_REMOVE

    def _set_status(self, message, is_error=False):
        self.status_label.set_text(message)
        self.status_label.remove_css_class("error")
        if is_error:
            self.status_label.add_css_class("error")

    def _set_busy(self, busy):
        self.busy = busy
        self._update_controls()

    def _active_device(self):
        index = self.device_picker.get_selected()
        if index >= len(self.devices):
            return None
        serial, state, model = self.devices[index]
        return (serial, model) if state == "device" else None

    def _update_controls(self):
        active = self._active_device() is not None
        ready = active and not self.busy
        incoming_call = bool(self.incoming_call_notification_key)
        self.dial_button.set_label("Raccrocher" if self.call_session_active else "Appeler depuis GNOME")
        if self.call_session_active:
            self.dial_button.remove_css_class("suggested-action")
            self.dial_button.add_css_class("destructive-action")
        else:
            self.dial_button.remove_css_class("destructive-action")
            self.dial_button.add_css_class("suggested-action")
        self.answer_button.set_visible(incoming_call)
        self.reject_call_button.set_visible(incoming_call)
        for widget in (
            self.dial_button,
            self.answer_button,
            self.reject_call_button,
            self.ring_phone_button,
            self.stop_phone_ring_button,
            self.scrcpy_button,
            self.apps_refresh_button,
            self.install_apk_button,
            self.sms_open_button,
            self.sms_refresh_button,
            self.up_button,
            self.files_refresh_button,
            self.mount_phone_storage_button,
            self.phone_link_button,
            self.photos_dcim_button,
            self.photos_pictures_button,
            self.mkdir_button,
            self.upload_button,
            self.device_profile_refresh_button,
            self.contacts_refresh_button,
            self.recents_refresh_button,
            self.recents_open_button,
            self.recents_more_button,
            self.adb_reboot_button,
            self.adb_recovery_button,
            self.adb_bootloader_button,
            self.adb_poweroff_button,
            self.adb_wake_button,
            self.adb_home_button,
            self.adb_back_button,
            self.adb_lock_button,
            self.adb_shell_run_button,
            self.adb_props_button,
            self.adb_logcat_button,
            self.adb_screenshot_button,
        ):
            widget.set_sensitive(ready)
        self.dial_button.set_sensitive(ready and (self.call_session_active or not incoming_call))
        mount = self.phone_mounts.get(self._active_device()[0]) if active else None
        mounted = bool(mount and mount["path"].is_mount())
        mounting = bool(mount and not mounted and mount["process"].poll() is None)
        self.mount_phone_storage_button.set_label(
            "Montage en cours…" if mounting else "Démonter de Nautilus" if mounted else "Monter dans Nautilus · ADB Wi-Fi"
        )
        self.mount_phone_storage_button.set_sensitive(ready and not mounting)
        self.number_entry.set_sensitive(ready)
        self.sms_number_entry.set_sensitive(ready)
        self.sms_body_view.set_sensitive(ready)
        self.sms_sidebar_list.set_sensitive(ready)
        self.sms_search.set_sensitive(ready)
        self.sms_new_button.set_sensitive(ready)
        self.apps_search.set_sensitive(ready)
        self.adb_shell_entry.set_sensitive(ready)
        self.file_list.set_sensitive(ready)
        self.phone_link_entry.set_sensitive(ready)
        self.download_button.set_sensitive(ready and self._selected_remote_file() is not None)
        self.device_picker.set_sensitive(bool(self.devices) and not self.busy)
        linked = False
        if self.droidlink_bridge:
            state = self.droidlink_bridge.snapshot()
            linked = state["paired"] or bool(state["code"])
            self.droidlink_pair_button.set_label(
                "Téléphone associé" if state["paired"] else "Renouveler le code" if state["code"] else "Démarrer l’appairage"
            )
            self.droidlink_pair_button.set_sensitive(ready and not state["paired"])
        else:
            self.droidlink_pair_button.set_sensitive(False)
            state = {"paired": False, "phone": {}}
        self.call_audio_bluetooth_button.set_sensitive(ready and not self.call_audio_busy)
        self.call_audio_hfp_button.set_sensitive(ready and not self.call_audio_hfp_busy)
        photo_path_available = self._selected_gallery_photo() is not None
        self.photos_gallery_button.set_sensitive(ready and state["paired"] and state["phone"].get("notificationAccess"))
        self.photos_open_button.set_sensitive(ready and photo_path_available)
        self.photos_download_button.set_sensitive(ready and photo_path_available)
        self.photos_external_button.set_sensitive(ready and photo_path_available)
        self.droidlink_disconnect_button.set_sensitive(linked and not self.busy)
        phone_relay_ready = ready and state["paired"] and state["phone"].get("notificationAccess")
        self.ring_phone_button.set_sensitive(phone_relay_ready)
        self.stop_phone_ring_button.set_sensitive(phone_relay_ready)
        for widget in (
            self.device_refresh_button,
            self.adb_start_button,
            self.adb_refresh_button,
            self.adb_reconnect_button,
            self.adb_pair_button,
            self.adb_connect_button,
            self.adb_stop_button,
            self.adb_version_button,
        ):
            widget.set_sensitive(not self.busy)

    def _on_device_selected(self, *_args):
        active = self._active_device()
        if self.embedded_stream_serial and (not active or self.embedded_stream_serial != active[0]):
            self._close_embedded_viewer()
        self._update_controls()
        self._droidlink_tunnel_serial = None
        self._ensure_droidlink_tunnel()
        self.remote_dir = "/sdcard"
        self.remote_path_label.set_text(self.remote_dir)
        self.contacts = []
        self.contacts_loaded = False
        self.contacts_display_limit = 100
        self.recents = []
        self.recents_loaded = False
        self.recents_offset = 0
        self.recents_has_more = False
        self.sms_conversations = []
        self.sms_messages = []
        self.sms_selected_thread = None
        self.sms_selected_number = ""
        self.sms_new_mode = False
        self.sms_waiting_for_contacts = False
        self.gallery_loaded = False
        self.gallery_photos = []
        self.gallery_cards.clear()
        self.gallery_thumbnail_cache_paths.clear()
        self.gallery_thumbnail_cache_dir = None
        while child := self.photos_flow.get_first_child():
            self.photos_flow.remove(child)
        self.photos_status.set_text("Choisis Actualiser pour charger les 2 000 photos récentes de ce téléphone.")
        self._render_sms_conversations()
        self._render_sms_thread()
        self._render_contacts()
        self._render_recents()
        if self._active_device():
            self.contacts_status.set_text("Chargement automatique des contacts du téléphone…")
            self.recents_status.set_text("Chargement des 50 derniers appels…")
            self._refresh_device_profile()
            self._refresh_contacts()
        else:
            self.phone_name_label.set_text("Aucun téléphone")
            self.phone_subtitle_label.set_text("Connectez un appareil ADB")
            self.phone_battery_label.set_text("")
            self.phone_battery_label.set_visible(False)
            self.phone_storage_label.set_text("")
            self.phone_storage_label.set_visible(False)
            self.contacts_status.set_text("Connectez un téléphone ADB pour charger les contacts.")
            self.recents_status.set_text("Connectez un téléphone ADB pour charger les appels.")
            self.phone_thumb.queue_draw()

    def _on_page_changed(self, *_args):
        self._sync_navigation_selection()
        if self.stack.get_visible_child_name() == "droidlink":
            self._droidlink_state_changed()
        self._load_current_page()

    def _load_current_page(self):
        if not self._active_device() or self.busy:
            return
        page = self.stack.get_visible_child_name()
        if page == "screen":
            self._refresh_apps()
        elif page == "sms":
            if not self.contacts_loaded:
                if not self.sms_waiting_for_contacts:
                    self.sms_waiting_for_contacts = True
                    self._refresh_contacts()
            elif not self.sms_conversations:
                self._refresh_sms()
        elif page == "files":
            self._load_directory()
        elif page == "gallery" and not self.gallery_loaded:
            self._load_photos_gallery()
        elif page == "calls" and not self.recents_loaded:
            self._refresh_recents()
        elif page == "contacts":
            if not self.contacts_loaded:
                self._refresh_contacts()

    def refresh_devices(self):
        if not self.adb:
            self._set_status("adb est introuvable. Installez Android Platform Tools.", True)
            return
        self._run_adb_command(["devices", "-l"], "Recherche des téléphones…", self._devices_result, needs_device=False)

    def _devices_result(self, result, error):
        if error:
            self.devices = []
            self._replace_device_model(["ADB indisponible"])
            self._set_status(f"ADB n’a pas démarré : {error}", True)
            self._update_controls()
            return
        if result.returncode != 0:
            self.devices = []
            self._replace_device_model(["ADB indisponible"])
            self._set_status((result.stderr or result.stdout).strip() or "Échec de la détection ADB", True)
            self._update_controls()
            return

        devices = []
        for line in result.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2:
                continue
            serial, state = fields[0], fields[1]
            details = dict(field.split(":", 1) for field in fields[2:] if ":" in field)
            model = details.get("model", serial).replace("_", " ")
            devices.append((serial, state, model))
        old = self._active_device()
        self.devices = devices
        labels = [
            f"{model} · {serial}" if state == "device" else f"{model} · {state}"
            for serial, state, model in devices
        ]
        selected = next((i for i, item in enumerate(devices) if old and item[0] == old[0]), None)
        if selected is None:
            selected = next((i for i, item in enumerate(devices) if item[1] == "device"), None)
        if selected is None:
            selected = 0 if devices else Gtk.INVALID_LIST_POSITION
        self._replace_device_model(labels or ["Aucun téléphone ADB"], selected)
        self._update_controls()
        if not devices:
            self._set_status("Aucun téléphone ADB détecté. Active le débogage sans fil et associe le téléphone.")
        elif any(state == "unauthorized" for _serial, state, _model in devices):
            self._set_status("Autorise le débogage Android sur le téléphone, puis actualise.")
        elif not any(state == "device" for _serial, state, _model in devices):
            self._set_status("Téléphone détecté, mais ADB n’est pas encore prêt. Vérifiez l’autorisation ou la connexion.", True)
        else:
            self._set_status(f"{sum(state == 'device' for _s, state, _m in devices)} téléphone(s) ADB prêt(s).")

    def _replace_device_model(self, labels, selected=Gtk.INVALID_LIST_POSITION):
        self.device_picker.handler_block(self._device_selected_handler)
        self.device_model = Gtk.StringList.new(labels)
        self.device_picker.set_model(self.device_model)
        self.device_picker.set_selected(selected)
        self.device_picker.handler_unblock(self._device_selected_handler)
        self._on_device_selected()

    def _refresh_device_profile(self, *_args):
        active = self._active_device()
        if not active or active[0] in self.phone_profile_loading:
            return
        serial = active[0]
        self.phone_profile_loading.add(serial)

        def worker():
            try:
                result = subprocess.run(
                    [self.adb, "-s", serial, "shell", "getprop"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                error = None if result.returncode == 0 else (result.stderr or result.stdout).strip()
                output = result.stdout
            except (OSError, subprocess.TimeoutExpired) as exc:
                output, error = "", str(exc)
            status_outputs = {}
            for name, args in (
                ("battery", ["shell", "dumpsys", "battery"]),
                ("storage", ["shell", "df", "-h", "/sdcard"]),
            ):
                try:
                    result = subprocess.run(
                        [self.adb, "-s", serial, *args],
                        capture_output=True,
                        text=True,
                        timeout=12,
                        check=False,
                    )
                    if result.returncode == 0:
                        status_outputs[name] = result.stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    continue
            GLib.idle_add(
                self._device_profile_result,
                serial,
                output,
                error,
                status_outputs.get("battery", ""),
                status_outputs.get("storage", ""),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _device_profile_result(self, serial, output, error, battery_output="", storage_output=""):
        self.phone_profile_loading.discard(serial)
        if not self._active_device() or self._active_device()[0] != serial:
            return GLib.SOURCE_REMOVE
        props = {}
        for line in output.splitlines():
            match = re.match(r"\[([^]]+)\]: \[(.*)\]", line)
            if match:
                props[match.group(1)] = match.group(2)
        profile = {
            "manufacturer": props.get("ro.product.manufacturer") or props.get("ro.product.brand") or "Android",
            "model": props.get("ro.product.marketname") or props.get("ro.product.vendor.marketname") or props.get("ro.product.model") or self._active_device()[1],
            "device": props.get("ro.product.device", ""),
            "android": props.get("ro.build.version.release", ""),
        }
        self.device_profiles[serial] = profile
        manufacturer = profile["manufacturer"].strip()
        model = profile["model"].strip().replace("_", " ")
        title = " ".join(part for part in (manufacturer, model) if part and manufacturer.casefold() not in model.casefold())
        self.phone_name_label.set_text(title or model or "Téléphone Android")
        details = [item for item in (profile["device"], f"Android {profile['android']}" if profile["android"] else "") if item]
        self.phone_subtitle_label.set_text(" · ".join(details) or ("Modèle détecté" if not error else "Modèle partiellement disponible"))
        battery_match = re.search(r"(?m)^\s*level:\s*(\d+)", battery_output)
        battery_text = f"Batterie · {battery_match.group(1)} %" if battery_match else ""
        self.phone_battery_label.set_text(battery_text)
        self.phone_battery_label.set_visible(bool(battery_text))
        storage_lines = [line.split() for line in storage_output.splitlines() if line.strip()]
        storage_values = storage_lines[-1] if len(storage_lines) > 1 else []
        storage_text = ""
        if len(storage_values) >= 4 and storage_values[1] and storage_values[2]:
            storage_text = f"Stockage · {storage_values[2]} / {storage_values[1]} utilisés"
        self.phone_storage_label.set_text(storage_text)
        self.phone_storage_label.set_visible(bool(storage_text))
        self.phone_thumb.set_tooltip_text(f"{manufacturer} {model}".strip())
        self.phone_thumb.queue_draw()
        return GLib.SOURCE_REMOVE

    def _append_digit(self, digit):
        self.number_entry.set_text(self.number_entry.get_text() + digit)
        self.number_entry.set_position(-1)

    def _refresh_contacts(self, *_args):
        self.contacts_display_limit = 100
        self.contacts_status.set_text("Lecture des contacts…")
        self._run_adb_command(
            _content_query_args(
                "content://com.android.contacts/data/phones",
                "contact_id:display_name:data1",
                sort="display_name COLLATE NOCASE ASC",
            ),
            "Lecture des contacts Android…",
            self._contacts_result,
        )

    def _contacts_result(self, result, error):
        self.contacts_loaded = True
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self.contacts = []
            self._render_contacts()
            self.contacts_status.set_text("Accès aux contacts refusé ou fournisseur indisponible. Autorisez l’accès sur le téléphone si Android le demande.")
            self._set_status(f"Contacts indisponibles : {detail}", True)
            self._render_sms_conversations()
            self._update_sms_contact_labels()
            self._contacts_ready_for_sms()
            self._load_current_page()
            return
        grouped = {}
        for row in _content_rows(result.stdout):
            name = row.get("display_name", "").strip() or "Contact sans nom"
            number = row.get("data1", "").strip()
            contact_id = row.get("contact_id", "").strip()
            key = ("id", contact_id) if contact_id else ("name", name.casefold())
            if not number:
                continue
            item = grouped.setdefault(key, {"name": name, "numbers": []})
            if number not in item["numbers"]:
                item["numbers"].append(number)
        self.contacts = sorted(
            ((item["name"], tuple(item["numbers"])) for item in grouped.values()),
            key=lambda item: item[0].casefold(),
        )
        self._render_contacts()
        self._update_contacts_status()
        self._set_status(f"{len(self.contacts)} contacts chargés, numéros regroupés.")
        self._render_sms_conversations()
        self._update_sms_contact_labels()
        self._contacts_ready_for_sms()
        self._load_current_page()

    def _contacts_ready_for_sms(self):
        if self.sms_waiting_for_contacts or (
            self.stack.get_visible_child_name() == "sms" and not self.sms_conversations
        ):
            self.sms_waiting_for_contacts = False
            self._refresh_sms()

    def _update_sms_contact_labels(self):
        if not self.sms_selected_number or not self.sms_messages:
            return
        conversation = next(
            (item for item in self.sms_conversations if str(item["thread_id"]) == str(self.sms_selected_thread)),
            None,
        )
        title = self._sms_conversation_label(conversation) if conversation else self._sms_display_name(self.sms_selected_number)
        self.sms_thread_title.set_text(f"{title} · {len(self.sms_messages)} messages")
        self.sms_recipient_label.set_text(f"Répondre au groupe · {title}" if self.sms_thread_is_group else f"Répondre à {title}")

    def _filtered_contacts(self):
        query = self.contact_search.get_text().strip().casefold()
        return [
            (name, numbers) for name, numbers in self.contacts
            if query in name.casefold() or any(query in number.casefold() for number in numbers)
        ]

    def _update_contacts_status(self):
        filtered = self._filtered_contacts()
        shown = min(len(filtered), self.contacts_display_limit)
        if not self.contacts:
            self.contacts_status.set_text("Aucun contact avec numéro trouvé.")
        elif len(filtered) == len(self.contacts):
            self.contacts_status.set_text(f"{shown} sur {len(self.contacts)} contacts affichés.")
        else:
            self.contacts_status.set_text(f"{shown} résultat(s) affiché(s) sur {len(filtered)}.")

    def _contacts_search_changed(self, *_args):
        self.contacts_display_limit = 100
        if self.contacts_search_source:
            GLib.source_remove(self.contacts_search_source)
        self.contacts_search_source = GLib.timeout_add(120, self._finish_contacts_search)

    def _finish_contacts_search(self):
        self.contacts_search_source = 0
        self._render_contacts()
        if self.contacts_loaded:
            self._update_contacts_status()
        return GLib.SOURCE_REMOVE

    def _show_more_contacts(self, *_args):
        self.contacts_display_limit += 100
        self._render_contacts()
        self._update_contacts_status()

    def _render_contacts(self):
        while child := self.contact_list.get_first_child():
            self.contact_list.remove(child)
        visible = self._filtered_contacts()
        shown = visible[:self.contacts_display_limit]
        for name, numbers in shown:
            row = Adw.ActionRow.new()
            row.set_title(GLib.markup_escape_text(name))
            row.set_subtitle(numbers[0] if len(numbers) == 1 else f"{numbers[0]} · +{len(numbers) - 1} autres")
            row.set_tooltip_text("\n".join(numbers))
            initials = "".join(part[0] for part in name.split()[:2] if part).upper() or "?"
            avatar = Gtk.Label(label=initials)
            avatar.add_css_class("contact-avatar")
            row.add_prefix(avatar)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if len(numbers) == 1:
                number = numbers[0]
                message_button = Gtk.Button.new_from_icon_name("mail-message-new-symbolic")
                message_button.add_css_class("flat")
                message_button.set_tooltip_text("Préparer un SMS")
                message_button.connect("clicked", lambda *_args, value=number: self._open_sms(value))
                button = Gtk.Button.new_from_icon_name("call-start-symbolic")
                button.add_css_class("flat")
                button.set_tooltip_text("Composer ce numéro")
                button.connect("clicked", lambda *_args, value=number: self._call_number(value))
            else:
                message_button = self._contact_number_menu(name, numbers, "sms")
                button = self._contact_number_menu(name, numbers, "call")
            actions.append(message_button)
            actions.append(button)
            row.add_suffix(actions)
            self.contact_list.append(row)
        if not shown:
            empty_row = Gtk.ListBoxRow()
            empty_row.set_activatable(False)
            empty_label = Gtk.Label(
                label="Aucun résultat" if self.contacts else "Les contacts apparaîtront ici après leur chargement.",
                xalign=0.5,
            )
            empty_label.add_css_class("dim-label")
            empty_label.set_margin_top(20)
            empty_label.set_margin_bottom(20)
            empty_row.set_child(empty_label)
            self.contact_list.append(empty_row)
        self.contacts_more_button.set_visible(len(visible) > self.contacts_display_limit)
        if self.contacts_more_button.get_visible():
            remaining = len(visible) - self.contacts_display_limit
            self.contacts_more_button.set_label(f"Afficher 100 autres contacts · {remaining} restants")

    def _contact_number_menu(self, name, numbers, action):
        icon_name = "mail-message-new-symbolic" if action == "sms" else "call-start-symbolic"
        button = Gtk.MenuButton()
        button.set_icon_name(icon_name)
        button.add_css_class("flat")
        button.set_tooltip_text("Choisir un numéro pour envoyer un SMS" if action == "sms" else "Choisir un numéro à composer")
        popover = Gtk.Popover.new()
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        choices.set_margin_top(5)
        choices.set_margin_bottom(5)
        choices.set_margin_start(5)
        choices.set_margin_end(5)
        for number in numbers:
            choice = Gtk.Button(label=number)
            choice.add_css_class("flat")
            callback = self._open_sms if action == "sms" else self._call_number
            choice.connect("clicked", lambda _button, value=number, cb=callback: (popover.popdown(), cb(value)))
            choices.append(choice)
        popover.set_child(choices)
        button.set_popover(popover)
        return button

    def _refresh_recents(self, *_args):
        self.recents = []
        self.recents_offset = 0
        self.recents_has_more = False
        self.recents_loaded = False
        self._render_recents()
        self.recents_status.set_text("Lecture du journal d’appels…")
        self._query_recents(0, append=False)

    def _query_recents(self, offset, append):
        uri = f"content://call_log/calls?limit={self.recents_page_size + 1}&offset={offset}"
        self._run_adb_command(
            _content_query_args(uri, "_id:number:type:date:duration:name", sort="date DESC"),
            "Lecture des appels récents…" if not append else "Chargement des appels suivants…",
            lambda result, error: self._recents_result(result, error, offset=offset, append=append),
            timeout=45,
        )

    def _recents_result(self, result, error, fallback=False, previous_error="", offset=0, append=False):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            if not fallback:
                uri = f"content://call_log/calls?limit={self.recents_page_size + 1}&offset={offset}"
                self._run_adb_command(
                    _content_query_args(uri, "_id:number:type:date:duration", sort="date DESC"),
                    "Nouvelle tentative du journal d’appels…",
                    lambda retry, retry_error: self._recents_result(
                        retry, retry_error, True, detail, offset, append
                    ),
                    timeout=45,
                )
                return
            detail = "; ".join(item for item in (previous_error, detail) if item)
            if not append:
                self.recents = []
                self.recents_loaded = True
                self._render_recents()
            self.recents_status.set_text(f"Lecture refusée par Android : {detail[:180] or 'permission ou fournisseur indisponible'}")
            self._set_status("Le journal d’appels est inaccessible à ADB. Android protège ces données avec READ_CALL_LOG.", True)
            return
        rows = _content_rows(result.stdout)
        provider_error = re.search(r"SecurityException|Permission Denial|unknown column|Error:", result.stdout, re.IGNORECASE)
        if not rows and provider_error:
            detail = result.stdout.strip()
            if not fallback:
                uri = f"content://call_log/calls?limit={self.recents_page_size + 1}&offset={offset}"
                self._run_adb_command(
                    _content_query_args(uri, "_id:number:type:date:duration", sort="date DESC"),
                    "Nouvelle tentative du journal d’appels…",
                    lambda retry, retry_error: self._recents_result(
                        retry, retry_error, True, detail, offset, append
                    ),
                    timeout=45,
                )
                return
            if not append:
                self.recents_loaded = True
            self.recents_status.set_text(f"Lecture refusée par Android : {detail[:180]}")
            self._set_status("Android refuse l’accès au journal d’appels via ADB. READ_CALL_LOG est une permission restreinte.", True)
            return
        types = {"1": "Entrant", "2": "Sortant", "3": "Manqué", "4": "Messagerie vocale", "5": "Refusé", "6": "Bloqué"}
        page_rows = rows[:self.recents_page_size]
        page_entries = []
        for row in page_rows:
            number = row.get("number", "").strip() or "Numéro masqué"
            raw_date = row.get("date", "")
            try:
                when = datetime.fromtimestamp(int(raw_date) / 1000).strftime("%d %b · %H:%M")
            except (TypeError, ValueError, OSError, OverflowError):
                when = ""
            name = row.get("name", "").strip()
            if not name or name.casefold() == "null":
                name = number
            page_entries.append((name, number, types.get(row.get("type", ""), "Appel"), when, row.get("_id", "")))
        self.recents = self.recents + page_entries if append else page_entries
        self.recents_offset = offset + len(page_entries)
        self.recents_has_more = len(rows) > self.recents_page_size
        self.recents_loaded = True
        self._render_recents()
        suffix = " · 50 autres disponibles" if self.recents_has_more else ""
        self.recents_status.set_text(f"{len(self.recents)} appel(s) récent(s) chargés{suffix}.")
        self._set_status("Journal d’appels actualisé.")

    def _load_more_recents(self, *_args):
        if self.recents_has_more and not self.busy:
            self._query_recents(self.recents_offset, append=True)

    def _open_android_call_log(self, *_args):
        self._run_adb_command(
            ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-t", "vnd.android.cursor.dir/calls"],
            "Ouverture du journal d’appels sur Android…",
            self._android_call_log_opened,
        )

    def _android_call_log_opened(self, result, error):
        if error or not result or result.returncode != 0:
            self._command_status(result, error, "Journal d’appels ouvert.")
            return
        active = self._active_device()
        if active:
            self._launch_scrcpy(active[0])
        self._set_status("Journal ouvert sur le téléphone; la liste interne ADB dépend de l’accès READ_CALL_LOG.")

    def _render_recents(self):
        while child := self.recent_list.get_first_child():
            self.recent_list.remove(child)
        for name, number, call_type, when, _call_id in self.recents:
            row = Adw.ActionRow.new()
            row.add_css_class("call-log-row")
            row.set_title(GLib.markup_escape_text(name))
            row.set_subtitle(" · ".join(part for part in (number, call_type, when) if part))
            icon = "call-incoming-symbolic" if call_type == "Entrant" else "call-outgoing-symbolic" if call_type == "Sortant" else "call-missed-symbolic" if call_type == "Manqué" else "call-start-symbolic"
            call_icon = Gtk.Image.new_from_icon_name(icon)
            if call_type == "Manqué":
                call_icon.add_css_class("error")
            else:
                call_icon.add_css_class("accent")
            row.add_prefix(call_icon)
            button = Gtk.Button.new_from_icon_name("call-start-symbolic")
            button.add_css_class("flat")
            button.set_tooltip_text("Rappeler")
            button.connect("clicked", lambda *_args, value=number: self._call_number(value))
            row.add_suffix(button)
            self.recent_list.append(row)
        if not self.recents:
            empty = Gtk.Label(label="Aucun appel récent chargé.", xalign=0.5)
            empty.add_css_class("dim-label")
            empty.set_margin_top(22)
            empty.set_margin_bottom(22)
            self.recent_list.append(empty)
        self.recents_more_button.set_visible(self.recents_has_more)
        self.recents_more_button.set_sensitive(self._active_device() is not None and not self.busy)

    def _call_number(self, number):
        self.number_entry.set_text(number)
        self.stack.set_visible_child_name("calls")
        self._place_call()

    def _open_sms(self, number):
        self.sms_new_mode = True
        self.sms_selected_thread = None
        self.sms_selected_number = ""
        self.sms_selected_recipients = []
        self.sms_thread_is_group = False
        self.sms_updating_list = True
        self.sms_sidebar_list.unselect_all()
        self.sms_updating_list = False
        self.sms_number_entry.set_text(number)
        self.sms_number_entry.set_visible(True)
        self.sms_recipient_label.set_text("Nouveau message · destinataire")
        self.sms_thread_title.set_text("Nouveau message")
        self.sms_messages = []
        self._render_sms_thread()
        self.stack.set_visible_child_name("sms")
        self.sms_body_view.grab_focus()

    def _refresh_sms(self, *_args):
        self.sms_status.set_text("Lecture des SMS, MMS et conversations de groupe…")
        self._run_adb_command(
            _content_query_args("content://mms-sms/canonical-addresses", "_id:address"),
            "Lecture des destinataires des conversations…",
            self._sms_canonical_result,
            timeout=90,
        )

    def _sms_canonical_result(self, result, error):
        self.sms_canonical_addresses = {}
        if not error and result and result.returncode == 0:
            for item in _content_rows(result.stdout):
                address_id = item.get("_id", "").strip()
                address = item.get("address", "").strip()
                if address_id.isdigit() and address and address.casefold() != "null":
                    self.sms_canonical_addresses[address_id] = address
        self._query_sms_conversations()

    def _query_sms_conversations(self):
        self._run_adb_command(
            _content_query_args(
                "content://mms-sms/conversations?simple=true",
                "_id:date:recipient_ids:message_count:read:type:snippet",
            ),
            "Lecture des conversations SMS et MMS Android…",
            self._sms_list_result,
            timeout=90,
        )

    def _sms_list_result(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self.sms_conversations = []
            self._render_sms_conversations()
            message = "Android n’a pas laissé ADB lire les conversations SMS/MMS. Vérifie que le téléphone est déverrouillé et que son fournisseur de messages est disponible."
            if detail:
                message += f" Détail : {detail[:220]}"
            self.sms_status.set_text(message)
            self._set_status("Historique SMS/MMS indisponible.", True)
            return

        conversations = []
        for item in _content_rows(result.stdout):
            thread_id = item.get("_id", "").strip()
            recipient_ids = re.findall(r"\d+", item.get("recipient_ids", ""))
            recipients = [
                self.sms_canonical_addresses[address_id]
                for address_id in recipient_ids
                if address_id in self.sms_canonical_addresses
            ]
            if not thread_id.isdigit():
                continue
            try:
                date = int(item.get("date", "0"))
                count = int(item.get("message_count", "0"))
            except ValueError:
                continue
            conversation = {
                "thread_id": thread_id,
                "recipient_ids": recipient_ids,
                "recipients": recipients,
                "is_group": len(recipient_ids) > 1,
                "number": ";".join(recipients),
                "date": date,
                "preview": "" if item.get("snippet", "").casefold() == "null" else item.get("snippet", ""),
                "unread": 0 if item.get("read", "1") == "1" else 1,
                "count": count,
            }
            conversations.append(conversation)
        conversations.sort(key=lambda conversation: conversation["date"], reverse=True)
        self.sms_conversations = conversations
        self._render_sms_conversations()
        total = sum(conversation["count"] for conversation in conversations)
        self.sms_status.set_text(f"{total:,} SMS et MMS · {len(conversations)} conversations · lus directement sur le téléphone.".replace(",", " "))
        self._set_status(f"Historique SMS/MMS chargé : {len(conversations)} conversations, groupes compris.")

    def _sms_display_name(self, number):
        number = str(number or "").strip()
        if not number or number.casefold() == "null":
            return "Numéro inconnu"
        normalized = re.sub(r"\D", "", number)
        for name, contact_numbers in self.contacts:
            for contact_number in contact_numbers:
                candidate = re.sub(r"\D", "", contact_number)
                if candidate == normalized and normalized:
                    return name
        if len(normalized) >= 10:
            suffix = normalized[-10:]
            for name, contact_numbers in self.contacts:
                for contact_number in contact_numbers:
                    candidate = re.sub(r"\D", "", contact_number)
                    if len(candidate) >= 10 and candidate[-10:] == suffix:
                        return name
        return number

    def _sms_conversation_numbers(self, conversation):
        recipients = conversation.get("recipients") or []
        if recipients:
            return recipients
        return [conversation["number"]] if conversation.get("number") else []

    def _sms_conversation_label(self, conversation):
        recipients = self._sms_conversation_numbers(conversation)
        labels = [self._sms_display_name(number) for number in recipients]
        is_group = conversation.get("is_group", len(conversation.get("recipient_ids", [])) > 1)
        if not labels:
            return "Groupe SMS" if is_group else "Conversation"
        if len(labels) == 1 and not is_group:
            return labels[0]
        if len(labels) <= 3:
            group_name = ", ".join(labels)
        else:
            group_name = f"{', '.join(labels[:2])} +{len(labels) - 2}"
        unknown = max(0, len(conversation.get("recipient_ids", [])) - len(labels))
        if unknown:
            group_name += f" +{unknown}"
        return f"Groupe · {group_name}"

    def _render_sms_conversations(self):
        while child := self.sms_sidebar_list.get_first_child():
            self.sms_sidebar_list.remove(child)
        self.sms_sidebar_rows.clear()
        query = self.sms_search.get_text().strip().casefold()
        conversations = [
            item for item in self.sms_conversations[:500]
            if not query
            or query in self._sms_conversation_label(item).casefold()
            or any(query in number.casefold() for number in self._sms_conversation_numbers(item))
            or any(query in self._sms_display_name(number).casefold() for number in self._sms_conversation_numbers(item))
            or query in item["preview"].casefold()
        ]
        for conversation in conversations:
            thread_id = str(conversation["thread_id"])
            display_name = self._sms_conversation_label(conversation)
            title = Gtk.Label(label=display_name, xalign=0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_hexpand(True)
            title.add_css_class("heading")
            preview_text = re.sub(r"\s+", " ", conversation["preview"]).strip()
            preview = Gtk.Label(
                label=preview_text or "Média ou SMS sans texte",
                xalign=0,
            )
            preview.set_ellipsize(Pango.EllipsizeMode.END)
            preview.add_css_class("dim-label")
            try:
                date_text = datetime.fromtimestamp(conversation["date"] / 1000).strftime("%H:%M" if datetime.fromtimestamp(conversation["date"] / 1000).date() == datetime.now().date() else "%d %b")
            except (ValueError, OSError, OverflowError):
                date_text = ""
            meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            meta.append(preview)
            preview.set_hexpand(True)
            if date_text:
                date_label = Gtk.Label(label=date_text, xalign=1)
                date_label.add_css_class("caption")
                date_label.add_css_class("dim-label")
                meta.append(date_label)
            if conversation["unread"]:
                unread = Gtk.Label(label=str(conversation["unread"]))
                unread.add_css_class("caption")
                unread.add_css_class("accent")
                meta.append(unread)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_margin_top(7)
            content.set_margin_bottom(7)
            content.set_margin_start(8)
            content.set_margin_end(8)
            content.append(title)
            content.append(meta)
            row = Gtk.ListBoxRow()
            row.set_name(thread_id)
            row.set_child(content)
            self.sms_sidebar_rows[thread_id] = row
            self.sms_sidebar_list.append(row)
        self.sms_sidebar_empty.set_text(
            "Aucune conversation" if not query else "Aucun résultat"
        )
        self.sms_sidebar_empty.set_visible(not conversations)

        thread_ids = [str(item["thread_id"]) for item in self.sms_conversations]
        visible_ids = [str(item["thread_id"]) for item in conversations]
        selected_thread = self.sms_selected_thread
        should_load = False
        if not self.sms_new_mode and selected_thread not in thread_ids:
            selected_thread = visible_ids[0] if visible_ids else None
            self.sms_selected_thread = selected_thread
            should_load = bool(selected_thread)
        elif not self.sms_new_mode and selected_thread is None and visible_ids:
            selected_thread = visible_ids[0]
            self.sms_selected_thread = selected_thread
            should_load = True

        self.sms_updating_list = True
        selected_row = self.sms_sidebar_rows.get(selected_thread)
        if selected_row:
            self.sms_sidebar_list.select_row(selected_row)
        else:
            self.sms_sidebar_list.unselect_all()
        self.sms_updating_list = False

        if should_load and selected_thread:
            GLib.idle_add(self._load_sms_thread, selected_thread)
        elif not self.sms_conversations:
            self.sms_thread_title.set_text("Aucune conversation SMS")
            self.sms_recipient_label.set_text(
                "Écris un numéro pour créer un SMS." if self.sms_new_mode
                else "Choisis une conversation pour répondre"
            )

    def _on_sms_search(self, *_args):
        self._render_sms_conversations()

    def _sms_sidebar_selected(self, _listbox, row):
        if self.sms_updating_list:
            return
        if row is None:
            return
        thread_id = row.get_name()
        if thread_id and thread_id != self.sms_selected_thread:
            self._load_sms_thread(thread_id)

    def _start_new_sms(self, *_args):
        self.sms_new_mode = True
        self.sms_selected_thread = None
        self.sms_selected_number = ""
        self.sms_selected_recipients = []
        self.sms_thread_is_group = False
        self.sms_updating_list = True
        self.sms_sidebar_list.unselect_all()
        self.sms_updating_list = False
        self.sms_number_entry.set_text("")
        self.sms_number_entry.set_visible(True)
        self.sms_recipient_label.set_text("Nouveau message · destinataire")
        self.sms_thread_title.set_text("Nouveau message")
        self.sms_messages = []
        self._render_sms_thread()
        self.sms_number_entry.grab_focus()

    def _load_sms_thread(self, thread_id):
        if not str(thread_id).isdigit():
            return
        self.sms_new_mode = False
        self.sms_number_entry.set_visible(False)
        self.sms_selected_thread = str(thread_id)
        conversation = next(
            (item for item in self.sms_conversations if str(item["thread_id"]) == str(thread_id)),
            {},
        )
        self.sms_selected_recipients = self._sms_conversation_numbers(conversation)
        self.sms_thread_is_group = conversation.get("is_group", len(self.sms_selected_recipients) > 1)
        self.sms_selected_number = ";".join(self.sms_selected_recipients)
        self.sms_thread_title.set_text("Chargement de la conversation…")
        self._run_adb_command(
            _content_query_args(
                f"content://mms-sms/conversations/{int(thread_id)}",
                "_id:thread_id:address:date:read:type:msg_box:subject:transport_type:body",
            ),
            "Lecture du fil complet SMS/MMS…",
            self._sms_thread_result,
            timeout=90,
        )

    def _sms_thread_result(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self.sms_thread_title.set_text("Conversation indisponible")
            self._set_status(f"Impossible de lire cette conversation SMS : {detail}", True)
            return
        self.sms_messages = []
        for item in _sms_rows(result.stdout):
            try:
                transport = item.get("transport_type", "sms").strip().casefold()
                message_type = item.get("msg_box", "") if transport == "mms" else item.get("type", "")
                message = {
                    "id": item.get("_id", "").strip(),
                    "transport": transport,
                    "address": item.get("address", "").strip(),
                    "date": int(item.get("date", "0")),
                    "type": int(message_type or "0"),
                    "body": item.get("body", ""),
                    "subject": item.get("subject", "").strip(),
                    "media": [],
                    "sender": "",
                }
            except ValueError:
                continue
            if message["transport"] == "sms" and message["type"] == 1:
                message["sender"] = message["address"]
            self.sms_messages.append(message)
        self.sms_messages.sort(key=lambda message: message["date"])
        message_count = len(self.sms_messages)
        if message_count > 300:
            self.sms_messages = self.sms_messages[-300:]
        discovered_recipients = []
        for message in self.sms_messages:
            address = message["address"]
            if address and address.casefold() != "null" and address not in discovered_recipients:
                discovered_recipients.append(address)
        if not self.sms_selected_recipients:
            self.sms_selected_recipients = discovered_recipients
        self.sms_thread_is_group = self.sms_thread_is_group or len(self.sms_selected_recipients) > 1
        if self.sms_messages:
            if not self.sms_selected_recipients and self.sms_messages[-1]["address"]:
                self.sms_selected_recipients = [self.sms_messages[-1]["address"]]
            self.sms_selected_number = ";".join(self.sms_selected_recipients)
            self.sms_number_entry.set_text(self.sms_selected_number)
            conversation = next(
                (item for item in self.sms_conversations if str(item["thread_id"]) == str(self.sms_selected_thread)),
                None,
            )
            title = self._sms_conversation_label(conversation) if conversation else (
                ", ".join(self._sms_display_name(number) for number in self.sms_selected_recipients)
                or "Conversation SMS"
            )
            self.sms_thread_title.set_text(f"{title} · {message_count} messages")
            self.sms_recipient_label.set_text(f"Répondre au groupe · {title}" if self.sms_thread_is_group else f"Répondre à {title}")
        else:
            self.sms_selected_number = ""
            self.sms_thread_title.set_text("Conversation vide")
            self.sms_recipient_label.set_text("Conversation vide")
        self._render_sms_thread()
        mms_ids = sorted({
            message["id"] for message in self.sms_messages
            if message["transport"] == "mms" and message["id"].isdigit()
        }, key=int)
        if mms_ids:
            where = f"msg_id IN ({','.join(mms_ids)})"
            self._run_adb_command(
                _content_query_args("content://mms/addr", "msg_id:address:type", where=where),
                "Lecture des participants MMS…",
                self._sms_mms_addresses_result,
                timeout=90,
            )

    def _sms_mms_addresses_result(self, result, error):
        if not error and result and result.returncode == 0:
            messages = {message["id"]: message for message in self.sms_messages}
            for item in _content_rows(result.stdout):
                message = messages.get(item.get("msg_id", "").strip())
                if message and item.get("type", "").strip() == "137":
                    address = item.get("address", "").strip()
                    if address and address.casefold() != "null" and address != "insert-address-token":
                        message["address"] = address
                        message["sender"] = address
        mms_ids = sorted({
            message["id"] for message in self.sms_messages
            if message["transport"] == "mms" and message["id"].isdigit()
        }, key=int)
        if not mms_ids:
            return
        where = f"mid IN ({','.join(mms_ids)})"
        self._run_adb_command(
            _content_query_args("content://mms/part", "mid:ct:name:text", where=where),
            "Lecture du texte et des médias MMS…",
            self._sms_mms_parts_result,
            timeout=90,
        )

    def _sms_mms_parts_result(self, result, error):
        if error or not result or result.returncode != 0:
            for message in self.sms_messages:
                if message["transport"] == "mms" and not message["body"] and not message["subject"] and not message["media"]:
                    message["media"].append("MMS · pièces jointes non lisibles")
            self._render_sms_thread()
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(
                f"Le contenu détaillé de certains MMS n’est pas accessible : {detail[:180] or 'Android ne fournit pas les pièces.'}",
                True,
            )
            return
        messages = {message["id"]: message for message in self.sms_messages}
        for item in _sms_rows(result.stdout):
            message = messages.get(item.get("mid", "").strip())
            if not message:
                continue
            content_type = item.get("ct", "").strip().casefold()
            name = item.get("name", "").strip()
            part_text = item.get("text", "").strip()
            if content_type.startswith("text/") and part_text and part_text.casefold() != "null":
                message["body"] = "\n".join(filter(None, (message["body"], part_text)))
            elif content_type.startswith("image/"):
                message["media"].append(f"Image jointe{f' · {name}' if name and name.casefold() != 'null' else ''}")
            elif content_type.startswith("video/"):
                message["media"].append(f"Vidéo jointe{f' · {name}' if name and name.casefold() != 'null' else ''}")
            elif content_type.startswith("audio/"):
                message["media"].append(f"Audio joint{f' · {name}' if name and name.casefold() != 'null' else ''}")
            elif name and name.casefold() != "null":
                message["media"].append(f"Fichier joint · {name}")
        for message in self.sms_messages:
            if message["transport"] == "mms" and not message["body"] and not message["subject"] and not message["media"]:
                message["media"].append("MMS · média sans texte")
        self._render_sms_thread()

    def _render_sms_thread(self):
        while child := self.sms_thread_list.get_first_child():
            self.sms_thread_list.remove(child)
        for message in self.sms_messages:
            incoming = message["type"] == 1
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            line.set_halign(Gtk.Align.START if incoming else Gtk.Align.END)
            bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            bubble.set_size_request(160, -1)
            bubble.set_margin_start(4)
            bubble.set_margin_end(4)
            bubble.set_margin_top(2)
            bubble.set_margin_bottom(2)
            bubble.add_css_class("sms-bubble-incoming" if incoming else "sms-bubble-outgoing")
            if self.sms_thread_is_group and incoming and message.get("sender"):
                sender = Gtk.Label(label=self._sms_display_name(message["sender"]), xalign=0)
                sender.add_css_class("caption")
                sender.add_css_class("accent")
                bubble.append(sender)
            content = "\n".join(filter(None, (
                message.get("subject", ""),
                message.get("body", ""),
                " · ".join(message.get("media", [])),
            )))
            if not content:
                content = "MMS · média joint" if message.get("transport") == "mms" else "SMS sans texte"
            text = Gtk.Label(label=content, xalign=0)
            text.set_wrap(True)
            text.set_max_width_chars(52)
            text.set_selectable(True)
            bubble.append(text)
            try:
                when = datetime.fromtimestamp(message["date"] / 1000).strftime("%d %b · %H:%M")
            except (ValueError, OSError, OverflowError):
                when = ""
            stamp = Gtk.Label(label=when, xalign=1)
            stamp.add_css_class("caption")
            stamp.add_css_class("dim-label")
            bubble.append(stamp)
            line.append(bubble)
            self.sms_thread_list.append(line)
        if self.sms_messages:
            GLib.idle_add(self._scroll_sms_thread_to_bottom)

    def _scroll_sms_thread_to_bottom(self):
        adjustment = self.sms_thread_scroll.get_vadjustment() if hasattr(self, "sms_thread_scroll") else None
        if adjustment:
            adjustment.set_value(adjustment.get_upper())
        return GLib.SOURCE_REMOVE

    def _send_sms_to_phone(self, *_args):
        active = self._active_device()
        if not active:
            return
        raw = self.sms_number_entry.get_text().strip()
        recipients = [item.strip() for item in re.split(r"[;,]", raw) if item.strip()]
        if not recipients or len(recipients) > 10 or any(not re.fullmatch(r"\+?[\d\s().-]+", item) for item in recipients):
            self._set_status("Entre un à dix numéros valides, séparés par des virgules.", True)
            return
        numbers = []
        for recipient in recipients:
            digits = re.sub(r"\D", "", recipient)
            if len(digits) < 3:
                self._set_status("Chaque numéro doit contenir au moins trois chiffres.", True)
                return
            numbers.append(("+" if recipient.startswith("+") else "") + digits)
        number = ";".join(numbers)
        buffer = self.sms_body_view.get_buffer()
        start, end = buffer.get_bounds()
        body = buffer.get_text(start, end, True).strip()
        if not body:
            self._set_status("Écris un message avant d’ouvrir l’application SMS.", True)
            return
        remote = [
            "am", "start", "-a", "android.intent.action.SENDTO", "-d", f"smsto:{number}",
            "--es", "sms_body", body,
        ]
        command = " ".join(shlex.quote(part) for part in remote)
        self._run_adb_command(
            ["shell", command],
            "Ouverture de Messages sur le téléphone…",
            self._sms_app_opened,
        )

    def _sms_app_opened(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Impossible d’ouvrir l’application SMS : {detail}", True)
            return
        active = self._active_device()
        if active:
            self._launch_scrcpy(active[0])
        self._set_status("Message préparé dans l’application Android. Vérifie-le et touche Envoyer sur le téléphone.")

    def _run_adb_command(self, args, message, callback, needs_device=True, timeout=25):
        if self.busy:
            return
        if not self.adb:
            self._set_status("adb est introuvable. Installez Android Platform Tools.", True)
            return
        command = [self.adb]
        if needs_device:
            active = self._active_device()
            if not active:
                self._set_status("Choisissez un téléphone ADB autorisé.", True)
                return
            command.extend(["-s", active[0]])
        command.extend(args)
        self._set_busy(True)
        self._set_status(message)

        def worker():
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                error = None
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = None
                error = str(exc)
            GLib.idle_add(finish, result, error)

        def finish(result, error):
            self._set_busy(False)
            callback(result, error)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=worker, daemon=True).start()

    def _call_button_clicked(self, *_args):
        if self.call_session_active:
            self._hangup_call()
        else:
            self._place_call()

    def _place_call(self, *_args):
        if not self._active_device():
            self._set_status("Connecte le téléphone à DroidDesk avant d’appeler.", True)
            return
        raw = self.number_entry.get_text().strip()
        if not re.fullmatch(r"\+?[\d\s().-]+", raw):
            self._set_status("Entrez un numéro avec chiffres, espaces, +, tirets ou parenthèses.", True)
            return
        digits = re.sub(r"\D", "", raw)
        number = ("+" if raw.startswith("+") else "") + digits
        if len(digits) < 3:
            self._set_status("Le numéro semble trop court.", True)
            return
        self.call_direction = "outgoing"
        self._run_adb_command(
            ["shell", "am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{number}"],
            f"Lancement de l’appel avec l’application Téléphone Android · {number}…",
            lambda result, error: self._call_action_result(
                result,
                error,
                f"Appel lancé vers {number} avec le téléphone Android.",
                start_audio=True,
            ),
        )

    def _answer_call(self, *_args):
        self._send_phone_call_key("KEYCODE_HEADSETHOOK", "Réponse envoyée au téléphone Android.")

    def _reject_call(self, *_args):
        self._send_phone_call_key("KEYCODE_ENDCALL", "Refus envoyé au téléphone Android.")

    def _hangup_call(self, *_args):
        self._send_phone_call_key("KEYCODE_ENDCALL", "Fin d’appel envoyée au téléphone Android.")

    def _send_phone_call_key(self, key, success):
        self._run_adb_command(
            ["shell", "input", "keyevent", key],
            "Commande d’appel envoyée au téléphone Android…",
            lambda result, error: self._call_key_action_result(key, result, error, success),
        )

    def _call_key_action_result(self, key, result, error, success):
        if not self._call_action_result(result, error, success):
            return
        if key == "KEYCODE_HEADSETHOOK":
            self.call_session_active = True
            self.call_direction = "incoming"
            self.active_call_notification_key = self.incoming_call_notification_key
            self.incoming_call_notification_key = None
            self._schedule_call_audio_bridge()
            self._update_controls()
        elif key == "KEYCODE_ENDCALL":
            self.incoming_call_notification_key = None
            self.active_call_notification_key = None
            self._finish_call_session()

    def _finish_call_session(self):
        self.call_session_active = False
        self.call_audio_should_stop = True
        self.call_audio_auto_retries = 0
        self._stop_call_audio_bridge()
        self._update_controls()

    def _call_action_result(self, result, error, success, start_audio=False):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "ADB n’a pas répondu.")
            self._set_status(f"Le téléphone Android a refusé la commande d’appel : {detail}", True)
            return False
        output = (result.stdout or "").strip()
        if "Permission Denial" in output or "Error:" in output:
            self._set_status(f"Android n’a pas exécuté la commande : {output[:240]}", True)
            return False
        self._set_status(success)
        if start_audio:
            self.call_session_active = True
            self.call_direction = "outgoing"
            self._schedule_call_audio_bridge()
            self._update_controls()
        return True

    def _load_call_recording_enabled(self):
        path = Path(GLib.get_user_config_dir()) / "droiddesk" / "settings.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return bool(value.get("record_calls", False))
        except (OSError, ValueError, AttributeError):
            return False

    def _save_call_recording_enabled(self, enabled):
        path = Path(GLib.get_user_config_dir()) / "droiddesk" / "settings.json"
        try:
            try:
                settings = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                settings = {}
            if not isinstance(settings, dict):
                settings = {}
            settings["record_calls"] = bool(enabled)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            self._set_status(f"Impossible d’enregistrer l’option d’appel : {error}", True)

    def _call_recording_toggled(self, switch, _param):
        self.call_recording_enabled = switch.get_active()
        self._save_call_recording_enabled(self.call_recording_enabled)
        if self.call_recording_enabled:
            self.call_recording_status.set_text(
                "Enregistrement activé · il démarrera quand un appel sera relié au son du PC."
            )
            self._maybe_start_call_recording()
        elif self.call_recording_process:
            self.call_recording_status.set_text("Finalisation de l’enregistrement…")
            self._stop_call_recording()
        else:
            self.call_recording_status.set_text("Enregistrement désactivé.")

    def _maybe_start_call_recording(self):
        if not self.call_recording_enabled or not self.call_session_active:
            return
        if self.call_recording_process:
            return
        if not self.call_audio_module_ids or not self.call_audio_record_sources:
            self.call_recording_status.set_text(
                "Activé · l’enregistrement commencera quand l’audio HFP sera relié."
            )
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.call_recording_status.set_text(
                "Enregistrement impossible : installe ffmpeg pour capturer les deux sens de l’appel."
            )
            return
        phone_source, microphone = self.call_audio_record_sources
        music_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
        directory = Path(music_dir or (Path.home() / "Music")) / "DroidDesk Calls"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S-%f")
            number = re.sub(r"[^0-9+]+", "", self.number_entry.get_text())[:24]
            label = f" - {number}" if number else ""
            path = directory / f"{timestamp} - {self.call_direction}{label}.wav"
            command = [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "pulse", "-i", phone_source,
                "-f", "pulse", "-i", microphone,
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=1:normalize=0[a]",
                "-map", "[a]", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(path),
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.call_recording_status.set_text(f"Enregistrement impossible : {error}")
            return
        self.call_recording_process = process
        self.call_recording_path = path
        self.call_recording_status.set_text(f"Démarrage de l’enregistrement · {path}")
        GLib.timeout_add(500, self._call_recording_start_check, process, path)

    def _call_recording_start_check(self, process, path):
        if self.call_recording_process is not process:
            return GLib.SOURCE_REMOVE
        if process.poll() is None:
            self.call_recording_status.set_text(f"● Enregistrement actif · {path}")
            return GLib.SOURCE_REMOVE
        _stdout, stderr = process.communicate()
        self.call_recording_process = None
        self.call_recording_path = None
        try:
            if path.exists() and path.stat().st_size <= 44:
                path.unlink()
        except OSError:
            pass
        detail = stderr.strip() or "ffmpeg s’est arrêté avant la capture audio."
        self.call_recording_status.set_text(f"Enregistrement impossible : {detail}")
        self._set_status(f"Enregistrement d’appel impossible : {detail}", True)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _finish_call_recording_process(process):
        try:
            process.send_signal(signal.SIGINT)
            _stdout, stderr = process.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                _stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate(timeout=2)
        except OSError as error:
            return str(error)
        return stderr.strip() if process.returncode not in (0, 255) else ""

    def _stop_call_recording(self):
        process = self.call_recording_process
        path = self.call_recording_path
        if not process:
            return
        self.call_recording_process = None
        self.call_recording_path = None

        def worker():
            error = self._finish_call_recording_process(process)
            GLib.idle_add(self._call_recording_stopped, path, error)

        threading.Thread(target=worker, daemon=True).start()

    def _call_recording_stopped(self, path, error=""):
        if error:
            self.call_recording_status.set_text(f"Enregistrement interrompu : {error}")
        elif path:
            self.call_recording_status.set_text(f"Enregistrement sauvegardé · {path}")
            self._set_status(f"Appel enregistré : {path}")
        else:
            self.call_recording_status.set_text("Enregistrement désactivé.")
        return GLib.SOURCE_REMOVE

    def _schedule_call_audio_bridge(self):
        self.call_audio_auto_retries = 3
        self.call_audio_should_stop = False
        self.call_audio_status.set_text("Appel lancé. Connexion de l’audio HFP aux écouteurs du PC…")
        if self.call_audio_module_ids and self.call_audio_record_sources:
            self._maybe_start_call_recording()
        GLib.timeout_add(1600, self._start_call_audio_bridge)

    def _toggle_call_audio_bridge(self, *_args):
        if self.call_audio_module_ids or self.call_audio_original_profile:
            self._stop_call_audio_bridge()
        else:
            self.call_audio_should_stop = False
            self._start_call_audio_bridge()

    def _confirm_enable_hfp_support(self, *_args):
        dialog = Adw.MessageDialog.new(
            self,
            "Activer l’audio mains libres",
            "DroidDesk va ajouter le rôle hfp_hf à la configuration WirePlumber de ta session et redémarrer WirePlumber. L’audio Bluetooth sera interrompu quelques secondes. Termine l’appel avant de continuer.",
        )
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("enable", "Activer HFP et redémarrer")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("enable")
        dialog.connect(
            "response",
            lambda current, response: (
                current.close(),
                self._enable_hfp_support() if response == "enable" else None,
            ),
        )
        dialog.present()

    def _enable_hfp_support(self):
        if self.call_audio_hfp_busy:
            return
        self.call_audio_hfp_busy = True
        self.call_audio_hfp_button.set_label("Activation…")
        self.call_audio_hfp_button.set_sensitive(False)
        self.call_audio_status.set_text("Activation du profil mains libres hfp_hf dans WirePlumber…")

        def worker():
            config_path = Path(GLib.get_user_config_dir()) / "wireplumber/wireplumber.conf.d/60-droiddesk-hfp.conf"
            config = """monitor.bluez.properties = {\n  bluez5.roles = [ a2dp_sink a2dp_source hfp_hf hfp_ag hsp_hs hsp_ag bap_sink bap_source ]\n  bluez5.hfphsp-backend = \"native\"\n}\n"""
            error = ""
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                if config_path.exists():
                    current = config_path.read_text(encoding="utf-8")
                    if current != config:
                        raise RuntimeError(
                            f"Le fichier {config_path} existe déjà avec un contenu différent; il n’a pas été modifié."
                        )
                else:
                    temporary = config_path.with_suffix(".tmp")
                    temporary.write_text(config, encoding="utf-8")
                    temporary.replace(config_path)
                result = subprocess.run(
                    ["systemctl", "--user", "restart", "wireplumber.service"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                if result.returncode != 0:
                    error = (result.stderr or result.stdout or "Redémarrage de WirePlumber impossible.").strip()
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                error = str(exc)
            GLib.idle_add(self._hfp_support_enabled, str(config_path), error)

        threading.Thread(target=worker, daemon=True).start()

    def _hfp_support_enabled(self, config_path, error):
        self.call_audio_hfp_busy = False
        self.call_audio_hfp_button.set_label("Activer HFP")
        self.call_audio_hfp_button.set_sensitive(self._active_device() is not None and not self.busy)
        if error:
            message = (
                f"Configuration écrite dans {config_path}, mais WirePlumber n’a pas redémarré : {error}. "
                "Reconnecte ta session GNOME pour appliquer le profil HFP."
            )
            self.call_audio_status.set_text(message)
            self._set_status(message, True)
        else:
            self.call_audio_status.set_text(
                "Rôle hfp_hf activé et WirePlumber redémarré. Reconnecte l’option Appels de Debian sur le téléphone; l’audio sera relié au prochain appel."
            )
            self.bluez.refresh()
            self._set_status("Profil mains libres HFP activé pour WirePlumber.")
        return GLib.SOURCE_REMOVE

    def _start_call_audio_bridge(self, *_args):
        if self.call_audio_busy or self.call_audio_module_ids:
            return GLib.SOURCE_REMOVE
        pactl = shutil.which("pactl")
        if not pactl:
            self.call_audio_status.set_text("pactl est introuvable : installe PipeWire avec sa compatibilité PulseAudio pour relier l’audio.")
            return GLib.SOURCE_REMOVE

        connected_hfp_devices = []
        for _path, interfaces in self.bluez.objects.items():
            device = interfaces.get("org.bluez.Device1")
            if not device or not device.get("Connected"):
                continue
            uuids = {str(uuid).casefold() for uuid in device.get("UUIDs", []) or []}
            if HFP_AUDIO_GATEWAY_UUID.casefold() not in uuids:
                continue
            connected_hfp_devices.append({
                "address": str(device.get("Address", "")),
                "name": str(device.get("Alias") or device.get("Name") or "Téléphone Bluetooth"),
            })
        active = self._active_device()
        phone_model = active[1] if active else ""
        self.call_audio_busy = True
        self.call_audio_status.set_text("Connexion du son d’appel du téléphone à la sortie audio GNOME…")
        self.call_audio_bluetooth_button.set_label("Connexion audio…")
        self.call_audio_bluetooth_button.set_sensitive(False)

        def worker():
            module_ids = []
            original_profile = self.call_audio_original_profile
            phone_card_name = ""
            phone_device = {"name": ""}
            candidate_cards = []
            output_name = ""
            mic_name = ""
            phone_source_name = ""
            error = ""

            def run(*args):
                result = subprocess.run(
                    [pactl, *args], capture_output=True, text=True, timeout=8, check=False
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "pactl a échoué").strip())
                return result.stdout.strip()

            def json_list(*args):
                output = run("-f", "json", "list", *args)
                parsed = json.loads(output)
                return parsed if isinstance(parsed, list) else []

            def address_key(value):
                match = re.search(
                    r"(?i)(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}", str(value)
                )
                return re.sub(r"[^0-9a-f]", "", match.group(0).casefold()) if match else ""

            def hfp_profiles(card):
                profiles = card.get("profiles", {})
                names = []
                for profile_name, profile in profiles.items():
                    key = profile_name.casefold().replace("_", "-")
                    is_hfp = any(token in key for token in (
                        "headset-head-unit", "handsfree-head-unit", "hfp-hf", "hsp-hs"
                    ))
                    available = profile.get("available") if isinstance(profile, dict) else None
                    if is_hfp and available not in ("no", False):
                        names.append(profile_name)
                priority = (
                    "headset-head-unit-msbc", "headset-head-unit",
                    "headset-head-unit-cvsd", "handsfree-head-unit",
                    "hfp-hf", "hsp-hs",
                )
                names.sort(key=lambda name: next(
                    (index for index, value in enumerate(priority) if value in name.casefold().replace("_", "-")),
                    len(priority),
                ))
                return names

            try:
                cards = json_list("cards")
                candidate_cards = []
                device_by_address = {
                    address_key(device["address"]): device
                    for device in connected_hfp_devices
                    if address_key(device["address"])
                }
                for card in cards:
                    if not str(card.get("name", "")).startswith("bluez_card."):
                        continue
                    profiles = hfp_profiles(card)
                    if not profiles:
                        continue
                    properties = card.get("properties", {}) or {}
                    identity_text = " ".join((
                        str(card.get("name", "")),
                        str(properties.get("device.description", "")),
                        str(properties.get("device.string", "")),
                        str(properties.get("api.bluez5.address", "")),
                    ))
                    match = device_by_address.get(address_key(identity_text))
                    if not match:
                        card_address = address_key(card.get("name", ""))
                        match = device_by_address.get(card_address)
                    if match:
                        candidate_cards.append((card, profiles, match))

                if len(candidate_cards) > 1 and phone_model:
                    model_key = re.sub(r"[^a-z0-9]", "", phone_model.casefold())
                    matching = [item for item in candidate_cards if model_key and model_key in re.sub(
                        r"[^a-z0-9]", "", item[2]["name"].casefold()
                    )]
                    if len(matching) == 1:
                        candidate_cards = matching
                if not candidate_cards:
                    if not connected_hfp_devices:
                        raise RuntimeError(
                            "Debian ne voit aucun téléphone Bluetooth connecté avec le profil mains libres HFP. "
                            "Dans Bluetooth, connecte le profil HFP du téléphone; le Bluetooth vert sur Android doit rester actif."
                        )
                    raise RuntimeError(
                        "PipeWire/WirePlumber ne publie pas de carte audio HFP pour le téléphone. "
                        "Clique sur « Activer HFP » hors appel, puis reconnecte le profil Appels de Debian dans les réglages Bluetooth Android."
                    )
                if len(candidate_cards) > 1:
                    raise RuntimeError(
                        "Plusieurs cartes HFP sont connectées. Déconnecte les autres téléphones et réessaie."
                    )

                card, profiles, phone_device = candidate_cards[0]
                phone_card_name = str(card.get("name", ""))
                active_profile = card.get("active_profile", {}) or {}
                active_profile_name = (
                    str(active_profile.get("name", ""))
                    if isinstance(active_profile, dict)
                    else str(active_profile)
                )
                profile_name = next(
                    (name for name in profiles if name == active_profile_name), profiles[0]
                )
                if profile_name != active_profile_name:
                    original_profile = (phone_card_name, active_profile_name)
                    run("set-card-profile", phone_card_name, profile_name)
                    time.sleep(0.9)

                sinks = json_list("sinks")
                sources = json_list("sources")
                card_index = str(card.get("index", ""))
                phone_sinks = [sink for sink in sinks if str(sink.get("card", "")) == card_index]
                phone_sources = [source for source in sources
                                 if str(source.get("card", "")) == card_index
                                 and not str(source.get("name", "")).endswith(".monitor")]
                if not phone_sources or not phone_sinks:
                    raise RuntimeError(
                        "Le profil HFP est sélectionné, mais PipeWire n’expose pas encore le canal audio bidirectionnel. "
                        "Garde un appel actif et appuie de nouveau sur « Relier l’audio aux écouteurs »."
                    )
                phone_source_name = str(phone_sources[0]["name"])

                default_sink = run("get-default-sink")
                local_sinks = [sink for sink in sinks if str(sink.get("card", "")) != card_index]
                output = next((sink for sink in local_sinks if sink.get("name") == default_sink), None)
                if not output and local_sinks:
                    output = local_sinks[0]
                if not output:
                    raise RuntimeError("Aucune sortie audio locale n’est disponible pour les écouteurs du PC.")
                output_name = str(output.get("name", ""))

                default_source = run("get-default-source")
                local_sources = [source for source in sources
                                 if str(source.get("card", "")) != card_index
                                 and not str(source.get("name", "")).endswith(".monitor")]
                microphone = next((source for source in local_sources
                                   if source.get("name") == default_source), None)
                if not microphone and local_sources:
                    microphone = local_sources[0]
                if not microphone:
                    raise RuntimeError("Aucun micro local n’est disponible pour transmettre ta voix au téléphone.")
                mic_name = str(microphone.get("name", ""))

                receive_id = run(
                    "load-module", "module-loopback",
                    f"source={phone_source_name}", f"sink={output_name}",
                    "latency_msec=80", "source_dont_move=true", "sink_dont_move=true",
                )
                module_ids.append(receive_id)
                transmit_id = run(
                    "load-module", "module-loopback",
                    f"source={mic_name}", f"sink={phone_sinks[0]['name']}",
                    "latency_msec=80", "source_dont_move=true", "sink_dont_move=true",
                )
                module_ids.append(transmit_id)
            except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, TypeError, KeyError) as exc:
                error = str(exc)
                for module_id in module_ids:
                    subprocess.run([pactl, "unload-module", module_id], capture_output=True, timeout=5, check=False)
                module_ids = []
                transient = any(term in error.casefold() for term in (
                    "ne voit aucun téléphone bluetooth connecté",
                    "ne publie pas de carte audio hfp",
                    "n’expose pas encore le canal audio",
                ))
                if original_profile and not (transient and self.call_audio_auto_retries > 0):
                    subprocess.run(
                        [pactl, "set-card-profile", original_profile[0], original_profile[1]],
                        capture_output=True, timeout=5, check=False,
                    )
                    original_profile = None
            GLib.idle_add(
                self._call_audio_setup_done,
                module_ids,
                original_profile,
                phone_card_name,
                phone_device["name"] if not error and candidate_cards else "",
                output_name,
                mic_name,
                phone_source_name,
                error,
            )

        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _call_audio_setup_done(self, module_ids, original_profile, card_name, device_name, output_name, mic_name, phone_source_name, error):
        self.call_audio_busy = False
        if error:
            transient = any(term in error.casefold() for term in (
                "ne voit aucun téléphone bluetooth connecté",
                "ne publie pas de carte audio hfp",
                "n’expose pas encore le canal audio",
            ))
            if transient and self.call_audio_auto_retries > 0:
                self.call_audio_auto_retries -= 1
                self.call_audio_original_profile = original_profile
                self.call_audio_status.set_text(
                    f"Le profil mains libres démarre; nouvelle tentative ({3 - self.call_audio_auto_retries}/3)…"
                )
                self.call_audio_bluetooth_button.set_label("Connexion audio…")
                self.call_audio_bluetooth_button.set_sensitive(False)
                GLib.timeout_add(1500, self._start_call_audio_bridge)
                return GLib.SOURCE_REMOVE
            self.call_audio_status.set_text(error)
            self.call_audio_original_profile = original_profile
            self.call_audio_bluetooth_button.set_label(
                "Restaurer le profil audio" if original_profile else "Relier l’audio aux écouteurs"
            )
            self.call_audio_bluetooth_button.set_sensitive(self._active_device() is not None and not self.busy)
            self._set_status(f"Audio d’appel non relié : {error}", True)
            self.call_audio_auto_retries = 0
            return GLib.SOURCE_REMOVE
        self.call_audio_module_ids = module_ids
        self.call_audio_original_profile = original_profile
        self.call_audio_phone_card = card_name
        self.call_audio_record_sources = (phone_source_name, mic_name)
        if self.call_audio_should_stop:
            self._stop_call_audio_bridge()
            return GLib.SOURCE_REMOVE
        self.call_audio_auto_retries = 0
        self.call_audio_status.set_text(
            f"Audio relié · téléphone {device_name} → {output_name} · micro du PC → téléphone ({mic_name})."
        )
        self.call_audio_bluetooth_button.set_label("Arrêter le relais audio")
        self.call_audio_bluetooth_button.set_sensitive(not self.busy)
        self._set_status("Audio d’appel relié aux écouteurs et au micro du PC.")
        self._maybe_start_call_recording()
        return GLib.SOURCE_REMOVE

    def _stop_call_audio_bridge(self, *_args):
        if self.call_audio_busy or not (self.call_audio_module_ids or self.call_audio_original_profile):
            return GLib.SOURCE_REMOVE
        pactl = shutil.which("pactl")
        modules = list(self.call_audio_module_ids)
        original_profile = self.call_audio_original_profile
        recording_process = self.call_recording_process
        recording_path = self.call_recording_path
        self.call_recording_process = None
        self.call_recording_path = None
        self.call_audio_busy = True
        self.call_audio_status.set_text("Arrêt du relais audio…")
        self.call_audio_bluetooth_button.set_label("Arrêt audio…")
        self.call_audio_bluetooth_button.set_sensitive(False)

        def worker():
            errors = []
            if recording_process:
                recording_error = self._finish_call_recording_process(recording_process)
                if recording_error:
                    errors.append(recording_error)
            if pactl:
                for module_id in modules:
                    result = subprocess.run(
                        [pactl, "unload-module", str(module_id)],
                        capture_output=True, text=True, timeout=5, check=False,
                    )
                    if result.returncode != 0 and "No such entity" not in (result.stderr or ""):
                        errors.append((result.stderr or result.stdout).strip())
                if original_profile:
                    card_name, profile_name = original_profile
                    if profile_name:
                        result = subprocess.run(
                            [pactl, "set-card-profile", card_name, profile_name],
                            capture_output=True, text=True, timeout=5, check=False,
                        )
                        if result.returncode != 0:
                            errors.append((result.stderr or result.stdout).strip())
            GLib.idle_add(
                self._call_audio_stopped,
                "; ".join(item for item in errors if item),
                recording_path,
            )

        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _call_audio_stopped(self, error="", recording_path=None):
        self.call_audio_busy = False
        self.call_audio_module_ids = []
        self.call_audio_original_profile = None
        self.call_audio_phone_card = ""
        self.call_audio_record_sources = None
        self.call_audio_auto_retries = 0
        self.call_audio_should_stop = False
        self.call_audio_status.set_text(
            f"Relais audio arrêté. {error}" if error else "Relais audio arrêté; la sortie GNOME est restaurée."
        )
        self.call_audio_bluetooth_button.set_label("Relier l’audio aux écouteurs")
        self.call_audio_bluetooth_button.set_sensitive(self._active_device() is not None and not self.busy)
        if recording_path:
            self.call_recording_status.set_text(f"Enregistrement sauvegardé · {recording_path}")
            self._set_status(f"Appel enregistré : {recording_path}")
        elif self.call_recording_enabled:
            self.call_recording_status.set_text(
                "Enregistrement activé · il démarrera quand un appel sera relié au son du PC."
            )
        else:
            self.call_recording_status.set_text("Enregistrement désactivé.")
        return GLib.SOURCE_REMOVE

    def _command_status(self, result, error, success):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Commande ADB échouée : {detail}", True)
        else:
            self._set_status(success)

    def _start_scrcpy(self, *_args):
        active = self._active_device()
        if active:
            self._launch_scrcpy(active[0])

    def _launch_scrcpy(self, serial, title=None):
        if not self.scrcpy:
            self._set_status("scrcpy est introuvable. Installez scrcpy, puis actualisez l’application.", True)
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self._set_status("FFmpeg est requis pour afficher scrcpy dans DroidDesk. Installe ffmpeg.", True)
            return False
        if not self.scrcpy_server or not Path(self.scrcpy_server).is_file():
            self._set_status("Le paquet scrcpy-server est introuvable. Réinstalle le paquet scrcpy Debian.", True)
            return False
        current = self.embedded_ffmpeg_process
        if (
            self.embedded_stream_serial == serial
            and current
            and current.poll() is None
            and self.embedded_scrcpy_process
            and self.embedded_scrcpy_process.poll() is None
        ):
            self.embedded_app_title.set_text(title or "Écran du téléphone")
            self._show_embedded_viewer()
            self._set_status("Le contrôle d’écran intégré est déjà ouvert.")
            return True

        self._stop_embedded_stream()
        generation = self.embedded_stream_generation
        self.embedded_stream_serial = serial
        self.embedded_frame_size = (0, 0)
        self.embedded_device_size = (0, 0)
        self.embedded_first_frame = False
        self.embedded_picture.set_paintable(None)
        self.embedded_screen_status.set_text("Préparation du flux vidéo scrcpy…")
        self.embedded_app_title.set_text(title or "Écran du téléphone")
        self._show_embedded_viewer()
        threading.Thread(
            target=self._prepare_embedded_stream,
            args=(serial, title, generation, ffmpeg),
            daemon=True,
        ).start()
        self._set_status("Préparation du flux vidéo scrcpy intégré…")
        return True

    def _prepare_embedded_stream(self, serial, title, generation, ffmpeg):
        server = None
        decoder = None
        stream_socket = None
        port = None
        try:
            version_result = subprocess.run(
                [self.scrcpy, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            version_output = f"{version_result.stdout or ''}\n{version_result.stderr or ''}"
            version_match = re.search(r"\bscrcpy\s+([0-9]+(?:\.[0-9]+)+)", version_output)
            if version_result.returncode != 0 or not version_match:
                raise RuntimeError("Impossible de déterminer la version du serveur scrcpy.")
            version = version_match.group(1)
            remote_server = "/data/local/tmp/droiddesk-scrcpy-server.jar"
            pushed = subprocess.run(
                [self.adb, "-s", serial, "push", self.scrcpy_server, remote_server],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if pushed.returncode != 0:
                detail = (pushed.stderr or pushed.stdout).strip()
                raise RuntimeError(f"Impossible de préparer scrcpy-server : {detail[:240]}")
            if generation != self.embedded_stream_generation:
                raise InterruptedError

            scid = secrets.randbelow(2**31)
            abstract_socket = f"scrcpy_{scid}"
            forwarded = subprocess.run(
                [self.adb, "-s", serial, "forward", "tcp:0", f"localabstract:{abstract_socket}"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if forwarded.returncode != 0:
                detail = (forwarded.stderr or forwarded.stdout).strip()
                raise RuntimeError(f"Impossible d’ouvrir le tunnel vidéo ADB : {detail[:240]}")
            port_match = re.search(r"\b(\d{2,5})\b", forwarded.stdout or "")
            if not port_match:
                raise RuntimeError("ADB n’a pas retourné le port du flux vidéo.")
            port = int(port_match.group(1))

            server_command = (
                f"CLASSPATH={shlex.quote(remote_server)} app_process / "
                f"com.genymobile.scrcpy.Server {version} scid={scid} "
                "tunnel_forward=true audio=false control=false cleanup=false "
                "raw_stream=true max_size=900"
            )
            server = subprocess.Popen(
                [self.adb, "-s", serial, "shell", server_command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + 15
            while generation == self.embedded_stream_generation and time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("scrcpy-server s’est arrêté avant d’envoyer la vidéo.")
                try:
                    stream_socket = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                    stream_socket.settimeout(1.0)
                    break
                except OSError:
                    time.sleep(0.15)
            if generation != self.embedded_stream_generation:
                raise InterruptedError
            if not stream_socket:
                raise RuntimeError("Délai dépassé en attendant la connexion vidéo scrcpy.")

            decoder = subprocess.Popen(
                [
                    ffmpeg,
                    "-hide_banner", "-loglevel", "error", "-flags", "low_delay",
                    "-f", "h264", "-i", "pipe:0", "-an",
                    "-vf", "fps=8,scale=720:-2",
                    "-q:v", "5", "-vcodec", "mjpeg",
                    "-f", "image2pipe", "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                start_new_session=True,
            )
            if generation != self.embedded_stream_generation:
                raise InterruptedError
            GLib.idle_add(
                self._embedded_stream_connected,
                generation,
                serial,
                title,
                port,
                stream_socket,
                server,
                decoder,
            )
            stream_socket = None
            server = None
            decoder = None
            port = None
        except InterruptedError:
            pass
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
            if generation == self.embedded_stream_generation:
                GLib.idle_add(self._embedded_stream_failed, generation, str(error)[:320])
        finally:
            if stream_socket:
                try:
                    stream_socket.close()
                except OSError:
                    pass
            for process in (decoder, server):
                if process:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            pass
            if port is not None:
                try:
                    subprocess.run(
                        [self.adb, "-s", serial, "forward", "--remove", f"tcp:{port}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass

    def _embedded_stream_connected(self, generation, serial, title, port, stream_socket, server, decoder):
        if generation != self.embedded_stream_generation:
            self._dispose_embedded_session(serial, port, stream_socket, server, decoder)
            return GLib.SOURCE_REMOVE
        self.embedded_stream_serial = serial
        self.embedded_forward_port = port
        self.embedded_stream_socket = stream_socket
        self.embedded_scrcpy_process = server
        self.embedded_ffmpeg_process = decoder
        self.embedded_app_title.set_text(title or "Écran du téléphone")
        self.embedded_screen_status.set_text("Flux scrcpy connecté · attente de la première image…")
        threading.Thread(
            target=self._pump_embedded_stream,
            args=(generation, stream_socket, decoder),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_embedded_frames,
            args=(generation, decoder),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._query_embedded_device_size,
            args=(serial, generation),
            daemon=True,
        ).start()
        self._set_status("Flux vidéo scrcpy connecté à DroidDesk.")
        return GLib.SOURCE_REMOVE

    def _embedded_stream_failed(self, generation, detail):
        if generation != self.embedded_stream_generation:
            return GLib.SOURCE_REMOVE
        self._stop_embedded_stream()
        self.embedded_screen_status.set_text(f"Flux scrcpy indisponible : {detail}")
        self._set_status(f"Impossible d’afficher l’écran Android : {detail}", True)
        return GLib.SOURCE_REMOVE

    def _pump_embedded_stream(self, generation, stream_socket, decoder):
        try:
            while generation == self.embedded_stream_generation:
                try:
                    chunk = stream_socket.recv(65_536)
                except socket.timeout:
                    if self.embedded_scrcpy_process and self.embedded_scrcpy_process.poll() is not None:
                        break
                    continue
                if not chunk:
                    break
                if not decoder.stdin:
                    break
                decoder.stdin.write(chunk)
                decoder.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        GLib.idle_add(self._embedded_stream_ended, generation, decoder.poll())

    def _dispose_embedded_session(self, serial, port, stream_socket, server, decoder):
        def worker():
            if stream_socket:
                try:
                    stream_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    stream_socket.close()
                except OSError:
                    pass
            for process in (decoder, server):
                if process and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            process.kill()
                            process.wait(timeout=1)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
            if port is not None:
                subprocess.run(
                    [self.adb, "-s", serial, "forward", "--remove", f"tcp:{port}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )

        threading.Thread(target=worker, daemon=True).start()

    def _show_embedded_viewer(self):
        self.apps_group.set_visible(False)
        self.install_group.set_visible(False)
        self.embedded_viewer.set_visible(True)

    def _close_embedded_viewer(self, *_args):
        self._stop_embedded_stream()
        self.embedded_viewer.set_visible(False)
        self.apps_group.set_visible(True)
        self.install_group.set_visible(True)
        self._update_controls()

    def _stop_embedded_stream(self):
        self.embedded_stream_generation += 1
        processes = (self.embedded_ffmpeg_process, self.embedded_scrcpy_process)
        stream_socket = self.embedded_stream_socket
        serial = self.embedded_stream_serial
        port = self.embedded_forward_port
        self.embedded_ffmpeg_process = None
        self.embedded_scrcpy_process = None
        self.embedded_stream_socket = None
        self.embedded_forward_port = None
        self.embedded_stream_serial = None
        if stream_socket:
            try:
                stream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                stream_socket.close()
            except OSError:
                pass
        for process in processes:
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        for process in processes:
            if not process:
                continue
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
        if serial and port is not None and self.adb:
            threading.Thread(
                target=self._remove_embedded_forward,
                args=(serial, port),
                daemon=True,
            ).start()

    def _remove_embedded_forward(self, serial, port):
        try:
            subprocess.run(
                [self.adb, "-s", serial, "forward", "--remove", f"tcp:{port}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return

    def _on_window_close_request(self, *_args):
        self._stop_embedded_stream()
        return False

    def _read_embedded_frames(self, generation, decoder):
        stream = decoder.stdout
        pending = bytearray()
        while stream and generation == self.embedded_stream_generation:
            try:
                chunk = stream.read(65_536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            pending.extend(chunk)
            while True:
                start = pending.find(b"\xff\xd8")
                if start < 0:
                    if len(pending) > 1:
                        del pending[:-1]
                    break
                if start:
                    del pending[:start]
                end = pending.find(b"\xff\xd9", 2)
                if end < 0:
                    if len(pending) > 8_000_000:
                        del pending[:-1]
                    break
                frame = bytes(pending[:end + 2])
                del pending[:end + 2]
                try:
                    pixbuf = self._read_pixbuf(frame)
                except (GLib.Error, ValueError):
                    continue
                if pixbuf:
                    GLib.idle_add(self._set_embedded_frame, generation, pixbuf)
        GLib.idle_add(self._embedded_stream_ended, generation, decoder.poll())

    def _set_embedded_frame(self, generation, pixbuf):
        if generation != self.embedded_stream_generation:
            return GLib.SOURCE_REMOVE
        self.embedded_frame_size = (pixbuf.get_width(), pixbuf.get_height())
        self.embedded_picture.set_pixbuf(pixbuf)
        if not self.embedded_first_frame:
            self.embedded_first_frame = True
            self.embedded_screen_status.set_text(
                "Connecté · touche ou glisse dans l’écran; clavier et navigation Android pris en charge."
            )
        return GLib.SOURCE_REMOVE

    def _embedded_stream_ended(self, generation, returncode):
        if generation != self.embedded_stream_generation:
            return GLib.SOURCE_REMOVE
        self._stop_embedded_stream()
        self.embedded_screen_status.set_text(
            "Le flux s’est arrêté. Vérifie la connexion ADB, scrcpy et FFmpeg, puis rouvre l’écran."
        )
        if returncode not in (0, None):
            self._set_status("Le flux vidéo scrcpy s’est arrêté prématurément.", True)
        return GLib.SOURCE_REMOVE

    def _query_embedded_device_size(self, serial, generation):
        try:
            result = subprocess.run(
                [self.adb, "-s", serial, "shell", "wm", "size"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            sizes = re.findall(r"(\d{2,5})x(\d{2,5})", result.stdout or "")
            if result.returncode == 0 and sizes:
                width, height = (int(value) for value in sizes[-1])
                GLib.idle_add(self._set_embedded_device_size, generation, width, height)
        except (OSError, subprocess.TimeoutExpired):
            return

    def _set_embedded_device_size(self, generation, width, height):
        if generation == self.embedded_stream_generation:
            self.embedded_device_size = (width, height)
        return GLib.SOURCE_REMOVE

    def _embedded_screen_point(self, x, y):
        video_width, video_height = self.embedded_frame_size
        widget_width = self.embedded_picture.get_width()
        widget_height = self.embedded_picture.get_height()
        if min(video_width, video_height, widget_width, widget_height) <= 0:
            return None
        scale = min(widget_width / video_width, widget_height / video_height)
        displayed_width = video_width * scale
        displayed_height = video_height * scale
        left = (widget_width - displayed_width) / 2
        top = (widget_height - displayed_height) / 2
        image_x = x - left
        image_y = y - top
        if image_x < 0 or image_y < 0 or image_x > displayed_width or image_y > displayed_height:
            return None
        device_width, device_height = self.embedded_device_size or (video_width, video_height)
        if device_width <= 0 or device_height <= 0:
            device_width, device_height = video_width, video_height
        if (video_width > video_height) != (device_width > device_height):
            device_width, device_height = device_height, device_width
        return (
            max(0, min(device_width - 1, round(image_x / scale * device_width / video_width))),
            max(0, min(device_height - 1, round(image_y / scale * device_height / video_height))),
        )

    def _embedded_drag_begin(self, _gesture, x, y):
        self.embedded_picture.grab_focus()
        self.embedded_drag_start = (x, y)

    def _embedded_drag_end(self, _gesture, offset_x, offset_y):
        start = self.embedded_drag_start
        self.embedded_drag_start = None
        if not start:
            return
        end = (start[0] + offset_x, start[1] + offset_y)
        first = self._embedded_screen_point(*start)
        last = self._embedded_screen_point(*end)
        if not first:
            return
        if (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2 < 144:
            self._send_embedded_input("tap", str(first[0]), str(first[1]))
        elif last:
            self._send_embedded_input(
                "swipe", str(first[0]), str(first[1]), str(last[0]), str(last[1]), "260"
            )

    def _embedded_key_pressed(self, _controller, keyval, _keycode, state):
        keycodes = {
            Gdk.KEY_Escape: "KEYCODE_BACK",
            Gdk.KEY_BackSpace: "KEYCODE_DEL",
            Gdk.KEY_Return: "KEYCODE_ENTER",
            Gdk.KEY_KP_Enter: "KEYCODE_ENTER",
            Gdk.KEY_Tab: "KEYCODE_TAB",
            Gdk.KEY_Left: "KEYCODE_DPAD_LEFT",
            Gdk.KEY_Right: "KEYCODE_DPAD_RIGHT",
            Gdk.KEY_Up: "KEYCODE_DPAD_UP",
            Gdk.KEY_Down: "KEYCODE_DPAD_DOWN",
        }
        if keyval in keycodes:
            self._send_embedded_key(keycodes[keyval])
            return True
        if state & Gdk.ModifierType.CONTROL_MASK:
            return False
        codepoint = Gdk.keyval_to_unicode(keyval)
        if codepoint:
            text = chr(codepoint)
            if text.isprintable():
                text = text.replace("%", "\\%").replace(" ", "%s")
                self._send_embedded_input("text", text)
                return True
        return False

    def _send_embedded_key(self, keycode):
        self._send_embedded_input("keyevent", keycode)

    def _send_embedded_input(self, *arguments):
        serial = self.embedded_stream_serial
        if not self.adb or not serial or not arguments:
            return
        remote_command = " ".join(shlex.quote(str(argument)) for argument in ("input", *arguments))

        def worker():
            with self.embedded_input_lock:
                try:
                    result = subprocess.run(
                        [self.adb, "-s", serial, "shell", remote_command],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout).strip()
                        GLib.idle_add(self._embedded_input_failed, detail[:200])
                except (OSError, subprocess.TimeoutExpired) as error:
                    GLib.idle_add(self._embedded_input_failed, str(error)[:200])

        threading.Thread(target=worker, daemon=True).start()

    def _embedded_input_failed(self, detail):
        if self.embedded_viewer.get_visible():
            self.embedded_screen_status.set_text(f"Commande tactile ADB échouée : {detail}")
        return GLib.SOURCE_REMOVE

    def _refresh_apps(self):
        self._run_adb_command(
            ["shell", "cmd", "package", "query-activities", "--brief", "--components", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER"],
            "Lecture des applications…",
            self._launcher_apps_result,
        )

    def _launcher_apps_result(self, result, error):
        packages = []
        if not error and result and result.returncode == 0:
            for line in result.stdout.splitlines():
                match = re.match(r"\s*([A-Za-z][A-Za-z0-9_.]+)/[A-Za-z0-9_.$]+", line)
                if match:
                    packages.append(match.group(1))
        if packages:
            self.app_packages = sorted(set(packages))
            self._render_apps()
            self._request_app_icons()
            self._set_status(f"{len(self.app_packages)} application(s) lançable(s).")
            return
        self._run_adb_command(
            ["shell", "pm", "list", "packages", "-3"],
            "La liste des lanceurs est indisponible; recherche des applications utilisateur…",
            self._apps_result,
        )

    def _apps_result(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Liste des applications indisponible : {detail}", True)
            return
        packages = sorted(
            line.removeprefix("package:").strip()
            for line in result.stdout.splitlines()
            if line.startswith("package:")
        )
        self.app_packages = packages
        self._render_apps()
        self._request_app_icons()
        self._set_status(f"{len(packages)} application(s) utilisateur(s) listée(s).")

    def _request_app_icons(self):
        if not self.droidlink_bridge:
            return
        state = self.droidlink_bridge.snapshot()
        if not state["paired"] or not state["phone"].get("notificationAccess"):
            return
        missing = [package for package in self.app_packages if package not in self.app_icons]
        for start in range(0, len(missing), 10):
            self.droidlink_bridge.enqueue_command(
                "app_icons", "", packages=missing[start : start + 10]
            )

    def _load_cached_app_icons(self):
        for path in self.app_icon_cache_dir.glob("*.png"):
            package = path.stem
            if not re.fullmatch(r"[A-Za-z0-9_.]{1,160}", package):
                continue
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
            except (OSError, GLib.Error):
                continue
            if pixbuf:
                self.app_icons[package] = pixbuf
                label_path = path.with_suffix(".label")
                try:
                    label = label_path.read_text(encoding="utf-8").strip()
                except OSError:
                    label = ""
                if label:
                    self.app_labels[package] = label[:100]

    @staticmethod
    def _write_cached_bytes(path, data):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            return

    def _on_apps_search(self, entry, *_args):
        self.app_search_text = entry.get_text().strip().casefold()
        self._render_apps()

    @staticmethod
    def _app_label(package):
        known = {
            "com.whatsapp": "WhatsApp",
            "org.telegram.messenger": "Telegram",
            "com.google.android.youtube": "YouTube",
            "com.google.android.apps.maps": "Google Maps",
            "com.android.chrome": "Chrome",
            "com.google.android.gm": "Gmail",
            "com.google.android.apps.photos": "Google Photos",
            "com.google.android.dialer": "Téléphone",
            "com.google.android.contacts": "Contacts",
            "com.facebook.katana": "Facebook",
            "com.instagram.android": "Instagram",
            "com.spotify.music": "Spotify",
            "com.discord": "Discord",
        }
        if package in known:
            return known[package]
        name = package.rsplit(".", 1)[-1]
        name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
        return name.replace("_", " ").strip().title() or package

    def _display_app_label(self, package):
        return self.app_labels.get(package) or self._app_label(package)

    @staticmethod
    def _app_icon(package):
        value = package.casefold()
        choices = (
            (("whatsapp", "telegram", "signal", "messag", "chat"), "internet-chat-symbolic"),
            (("camera", "photo", "gallery"), "camera-photo-symbolic"),
            (("music", "spotify", "audio"), "audio-x-generic-symbolic"),
            (("maps", "navigation"), "mark-location-symbolic"),
            (("phone", "dialer", "contacts"), "phone-symbolic"),
            (("browser", "chrome", "firefox", "webview"), "web-browser-symbolic"),
            (("mail", "gmail", "outlook"), "mail-unread-symbolic"),
            (("file", "drive", "storage"), "folder-symbolic"),
            (("video", "youtube", "movie"), "video-x-generic-symbolic"),
            (("game", "play"), "applications-games-symbolic"),
        )
        return next((icon for tokens, icon in choices if any(token in value for token in tokens)), "application-x-executable-symbolic")

    def _render_apps(self):
        while child := self.apps_grid.get_first_child():
            self.apps_grid.remove(child)
        packages = [package for package in self.app_packages if self.app_search_text in package.casefold() or self.app_search_text in self._display_app_label(package).casefold()]
        if not packages:
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            empty.add_css_class("app-tile")
            empty.set_size_request(190, 224)
            empty.set_valign(Gtk.Align.START)
            empty_label = Gtk.Label(label="Aucune application trouvée")
            empty_label.add_css_class("heading")
            empty.append(empty_label)
            hint = Gtk.Label(label="Actualise la liste ou change le filtre.")
            hint.add_css_class("dim-label")
            empty.append(hint)
            self.apps_grid.append(empty)
        for package in packages:
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tile.add_css_class("app-tile")
            tile.set_size_request(190, 224)
            tile.set_valign(Gtk.Align.START)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            actions.append(spacer)
            menu_button = Gtk.MenuButton.new()
            menu_button.set_icon_name("view-more-symbolic")
            menu_button.set_tooltip_text("Informations, désinstallation")
            menu_button.set_popover(self._app_context_menu(package))
            actions.append(menu_button)
            tile.append(actions)

            launch_button = Gtk.Button()
            launch_button.add_css_class("flat")
            launch_button.set_hexpand(True)
            launch_button.set_vexpand(True)
            launch_button.set_tooltip_text(f"Ouvrir {self._display_app_label(package)}")
            launch_button.connect("clicked", lambda _button, value=package: self._launch_app(value))
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            content.set_halign(Gtk.Align.CENTER)
            frame = Gtk.Box()
            frame.add_css_class("app-icon-frame")
            icon = Gtk.Image()
            pixbuf = self.app_icons.get(package)
            if pixbuf:
                icon.set_from_pixbuf(pixbuf.scale_simple(144, 144, GdkPixbuf.InterpType.HYPER) or pixbuf)
            else:
                icon.set_from_icon_name(self._app_icon(package))
                icon.set_pixel_size(128)
            frame.set_halign(Gtk.Align.CENTER)
            frame.set_valign(Gtk.Align.CENTER)
            frame.append(icon)
            content.append(frame)
            label = Gtk.Label(label=self._display_app_label(package))
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(20)
            label.add_css_class("app-tile-name")
            content.append(label)
            launch_button.set_child(content)
            tile.append(launch_button)
            context_click = Gtk.GestureClick.new()
            context_click.set_button(3)
            context_click.connect("pressed", self._show_app_context, package, tile)
            tile.add_controller(context_click)
            self.apps_grid.append(tile)
        if self.app_packages:
            received = sum(package in self.app_icons for package in self.app_packages)
            if received == len(self.app_packages):
                self.apps_icon_status.set_text(f"Icônes originales du téléphone · {received} applications")
            else:
                state = self.droidlink_bridge.snapshot() if self.droidlink_bridge else {"paired": False, "phone": {}}
                if not state["paired"] or not state["phone"].get("notificationAccess"):
                    self.apps_icon_status.set_text(
                        f"{received}/{len(self.app_packages)} icônes chargées · associe DroidLink et autorise l’accès aux notifications pour recevoir les icônes originales."
                    )
                else:
                    self.apps_icon_status.set_text(
                        f"Chargement des icônes Android… {received}/{len(self.app_packages)}"
                    )

    def _app_context_menu(self, package):
        popover = Gtk.Popover.new()
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.set_margin_top(5)
        menu.set_margin_bottom(5)
        menu.set_margin_start(5)
        menu.set_margin_end(5)
        options = (
            ("Ouvrir", lambda: self._launch_app(package)),
            ("Informations de l’application", lambda: self._open_app_info(package)),
            ("Désinstaller…", lambda: self._confirm_app_uninstall(package)),
        )
        for label, callback in options:
            item = Gtk.Button(label=label)
            item.add_css_class("flat")
            item.set_halign(Gtk.Align.FILL)
            item.connect("clicked", lambda _button, cb=callback: (popover.popdown(), cb()))
            menu.append(item)
        popover.set_child(menu)
        return popover

    def _show_app_context(self, _gesture, _presses, _x, _y, package, anchor):
        popover = self._app_context_menu(package)
        popover.set_parent(anchor)
        popover.connect("closed", lambda current: current.unparent() if current.get_parent() else None)
        popover.popup()

    def _confirm_app_uninstall(self, package):
        dialog = Gtk.Dialog(title="Désinstaller l’application ?", transient_for=self, modal=True)
        content = dialog.get_content_area()
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(18)
        content.set_margin_end(18)
        text = Gtk.Label(
            label=f"{self._display_app_label(package)} ({package}) sera retirée du profil Android actif. Une application système pourra être restaurée par Android.",
            xalign=0,
        )
        text.set_wrap(True)
        content.append(text)
        dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
        dialog.add_button("Désinstaller", Gtk.ResponseType.ACCEPT)

        def response(current, answer):
            current.destroy()
            if answer == Gtk.ResponseType.ACCEPT:
                self._run_adb_command(
                    ["shell", "pm", "uninstall", "--user", "0", package],
                    f"Désinstallation de {self._display_app_label(package)}…",
                    lambda result, error: self._uninstall_app_result(package, result, error),
                    timeout=60,
                )

        dialog.connect("response", response)
        dialog.present()

    def _uninstall_app_result(self, package, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Désinstallation impossible : {detail}", True)
            return
        self._set_status(f"{self._display_app_label(package)} retirée du profil Android.")
        self._refresh_apps()

    def _open_app_info(self, package):
        active = self._active_device()
        if not active:
            self._set_status("Choisis un téléphone ADB pour afficher les informations de l’app.", True)
            return
        self._run_adb_command(
            ["shell", "am", "start", "-a", "android.settings.APPLICATION_DETAILS_SETTINGS", "-d", f"package:{package}"],
            f"Ouverture des informations de {self._app_label(package)}…",
            lambda result, error: self._app_info_opened(active[0], package, result, error),
        )

    def _app_info_opened(self, serial, package, result, error):
        if error or not result or result.returncode != 0:
            self._command_status(result, error, "Informations ouvertes sur le téléphone.")
            return
        self._launch_scrcpy(serial, title=f"Infos · {self._display_app_label(package)}")

    def _launch_app(self, package):
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
            self._set_status("Nom de paquet invalide.", True)
            return
        active = self._active_device()
        if not active:
            self._set_status("Choisis un téléphone ADB pour ouvrir l’application.", True)
            return
        self._run_adb_command(
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            f"Lancement de {package}…",
            lambda result, error: self._app_launch_result(active[0], package, result, error),
        )

    def _app_launch_result(self, serial, package, result, error):
        if error or not result or result.returncode != 0:
            self._command_status(result, error, f"Application lancée : {package}")
            return
        label = self._display_app_label(package)
        if self._launch_scrcpy(serial, title=label):
            self._set_status(f"{label} ouverte dans l’écran intégré de DroidDesk.")

    def _choose_apk(self, *_args):
        chooser = Gtk.FileChooserNative.new(
            "Choisir une application Android",
            self,
            Gtk.FileChooserAction.OPEN,
            "Installer",
            "Annuler",
        )
        apk_filter = Gtk.FileFilter()
        apk_filter.set_name("Paquets Android (*.apk)")
        apk_filter.add_pattern("*.apk")
        chooser.add_filter(apk_filter)

        def response(dialog, answer):
            if answer == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file and file.get_path():
                    self._install_apk(file.get_path())
            dialog.destroy()

        chooser.connect("response", response)
        chooser.show()

    def _install_apk(self, path):
        self._run_adb_command(
            ["install", "-r", path],
            "Installation de l’application sur Android…",
            lambda result, error: self._command_status(result, error, "Installation terminée."),
            timeout=90,
        )

    def _send_link_to_phone(self, *_args):
        if not self._active_device():
            self._set_status("Connecte le téléphone avant de lui envoyer un lien.", True)
            return
        url = self.phone_link_entry.get_text().strip()
        if not re.fullmatch(r"https?://[^\s]+", url, re.IGNORECASE):
            self._set_status("Entre une adresse Web complète qui commence par http:// ou https://.", True)
            return
        command = "am start -a android.intent.action.VIEW -d " + shlex.quote(url)
        self._run_adb_command(
            ["shell", command],
            "Ouverture du lien sur Android…",
            lambda result, error: self._command_status(result, error, "Lien ouvert dans le navigateur Android."),
        )

    def _remote_path(self, path):
        normalized = posixpath.normpath(path)
        if normalized != "/sdcard" and not normalized.startswith("/sdcard/"):
            raise ValueError("Le navigateur est limité au stockage partagé /sdcard.")
        return normalized

    def _load_directory(self):
        try:
            self.remote_dir = self._remote_path(self.remote_dir)
        except ValueError as error:
            self._set_status(str(error), True)
            return
        self.remote_path_label.set_text(self.remote_dir)
        command = "ls -1Ap " + shlex.quote(self.remote_dir)
        self._run_adb_command(
            ["shell", command],
            f"Lecture de {self.remote_dir}…",
            self._directory_result,
        )

    def _directory_result(self, result, error):
        while child := self.file_list.get_first_child():
            self.file_list.remove(child)
        self.file_records.clear()
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Dossier inaccessible : {detail}", True)
            self._update_controls()
            return
        for listed in result.stdout.splitlines():
            entry = listed.rstrip("\r\n")
            if not entry or entry in (".", "..", "./", "../"):
                continue
            is_dir = entry.endswith("/")
            name = entry[:-1] if is_dir else entry
            if not name or "/" in name or "\x00" in name:
                continue
            remote_path = self._remote_path(posixpath.join(self.remote_dir, name))
            row = Gtk.ListBoxRow()
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            item.set_margin_top(8)
            item.set_margin_bottom(8)
            item.set_margin_start(10)
            item.set_margin_end(10)
            icon = Gtk.Image.new_from_icon_name("folder-symbolic" if is_dir else "text-x-generic-symbolic")
            item.append(icon)
            label = Gtk.Label(label=name, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(True)
            item.append(label)
            row.set_child(item)
            self.file_list.append(row)
            self.file_records[row] = (name, is_dir, remote_path)
        self._set_status(f"{len(self.file_records)} élément(s) dans {self.remote_dir}.")
        self._update_controls()

    def _selected_remote_file(self):
        row = self.file_list.get_selected_row()
        record = self.file_records.get(row) if row else None
        if record and not record[1]:
            return record[2]
        return None

    def _on_file_selected(self, *_args):
        self._update_controls()

    def _on_file_activated(self, _list, row):
        record = self.file_records.get(row)
        if record and record[1]:
            self.remote_dir = record[2]
            self._load_directory()
        elif record and Path(record[0]).suffix.lower() in {
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif",
        }:
            self._preview_remote_image(record[2])

    def _open_photo_folder(self, path):
        self.remote_dir = self._remote_path(path)
        self._load_directory()

    def _mount_phone_storage(self, *_args):
        active = self._active_device()
        if not active:
            self._set_status("Connecte le téléphone à ADB sans fil avant de monter son stockage.", True)
            return
        serial = active[0]
        current = self.phone_mounts.get(serial)
        if current:
            if current["path"].is_mount():
                self._unmount_phone_storage(serial)
            elif current["process"].poll() is None:
                self._set_status("Le montage ADB Wi-Fi démarre encore…")
            else:
                self.phone_mounts.pop(serial, None)
                self._mount_phone_storage()
            return
        if ":" not in serial:
            self._set_status("Ce téléphone n’est pas connecté en ADB Wi-Fi. Active Débogage sans fil et associe-le dans DroidDesk → ADB.", True)
            return
        if not self.madbfs:
            self._set_status("Le montage Nautilus sans câble requiert madbfs (FUSE). Installe madbfs, puis relance DroidDesk.", True)
            return
        fusermount = shutil.which("fusermount3") or shutil.which("fusermount")
        if not fusermount:
            self._set_status("FUSE est requis pour monter le téléphone dans Nautilus.", True)
            return
        try:
            runtime = Path(GLib.get_user_runtime_dir()) / "droiddesk" / "mounts"
            runtime.mkdir(parents=True, exist_ok=True)
            safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
            mount_path = runtime / safe_serial
            mount_path.mkdir(parents=True, exist_ok=True)
            mount_env = os.environ.copy()
            mount_env["ANDROID_SERIAL"] = serial
            mount_env["PATH"] = str(Path(self.adb).parent) + os.pathsep + mount_env.get("PATH", "")
            process = subprocess.Popen(
                [self.madbfs, f"--serial={serial}", "--root=/sdcard", "-o", "auto_unmount", "-f", str(mount_path)],
                env=mount_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.phone_mounts[serial] = {"path": mount_path, "process": process, "fusermount": fusermount}
        except OSError as error:
            self._set_status(f"Impossible de démarrer madbfs : {error}", True)
            return
        self.mount_phone_storage_button.set_label("Montage en cours…")
        self.mount_phone_storage_button.set_sensitive(False)
        self._set_status("Montage de /sdcard par ADB Wi-Fi…")

        def wait_for_mount():
            for _attempt in range(100):
                if mount_path.is_mount():
                    GLib.idle_add(self._open_phone_mount, serial, mount_path)
                    return
                if process.poll() is not None:
                    GLib.idle_add(self._phone_mount_failed, serial)
                    return
                time.sleep(0.1)
            GLib.idle_add(self._phone_mount_failed, serial)

        threading.Thread(target=wait_for_mount, daemon=True).start()

    def _open_phone_mount(self, serial, mount_path):
        mount = self.phone_mounts.get(serial)
        if mount:
            self.mount_phone_storage_button.set_label("Démonter de Nautilus")
            self.mount_phone_storage_button.set_sensitive(True)
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(mount_path)).get_uri(), None)
            self._set_status(f"Stockage /sdcard ouvert dans Nautilus via ADB Wi-Fi · {serial}.")
        except GLib.Error as error:
            self._set_status(f"Montage prêt, mais Nautilus n’a pas pu l’ouvrir : {error.message}", True)
        return GLib.SOURCE_REMOVE

    def _phone_mount_failed(self, serial):
        mount = self.phone_mounts.pop(serial, None)
        if mount and mount["process"].poll() is None:
            mount["process"].terminate()
        self.mount_phone_storage_button.set_label("Monter dans Nautilus · ADB Wi-Fi")
        self.mount_phone_storage_button.set_sensitive(self._active_device() is not None and not self.busy)
        self._set_status("madbfs n’a pas monté le stockage. Vérifie ADB Wi-Fi, FUSE et l’autorisation du téléphone.", True)
        return GLib.SOURCE_REMOVE

    def _unmount_phone_storage(self, serial):
        mount = self.phone_mounts.get(serial)
        if not mount:
            return
        path = mount["path"]
        try:
            if path.is_mount():
                result = subprocess.run(
                    [mount["fusermount"], "-u", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                if result.returncode != 0:
                    self._set_status(f"Démontage impossible : {(result.stderr or result.stdout).strip()}", True)
                    return
            process = mount["process"]
            if process.poll() is None:
                process.terminate()
            self.phone_mounts.pop(serial, None)
            self.mount_phone_storage_button.set_label("Monter dans Nautilus · ADB Wi-Fi")
            self._set_status("Stockage Android démonté de Nautilus.")
        except (OSError, subprocess.TimeoutExpired) as error:
            self._set_status(f"Démontage impossible : {error}", True)
        self._update_controls()

    @staticmethod
    def _safe_media_path(path):
        normalized = posixpath.normpath(str(path))
        if normalized.startswith(("/storage/", "/sdcard/")) and "/../" not in f"{normalized}/" and "\x00" not in normalized:
            return normalized
        return None

    def _load_photos_gallery(self, *_args):
        if not self.droidlink_bridge:
            self._set_status("Le pont DroidLink n’est pas disponible.", True)
            return
        state = self.droidlink_bridge.snapshot()
        if not state["paired"] or not state["phone"].get("notificationAccess"):
            self.photos_status.set_text("Associe DroidLink, active le relais de notifications, puis autorise Photos dans l’application Android.")
            self._set_status("Associe d’abord DroidLink pour charger la galerie.", True)
            return
        if not self.droidlink_bridge.enqueue_command("photo_list", ""):
            self._set_status("Impossible d’envoyer la demande de galerie.", True)
            return
        self.photos_status.set_text("Lecture des photos récentes sur le téléphone…")
        self._set_status("DroidLink prépare la galerie du téléphone…")

    def _populate_photos_gallery(self, items):
        child = self.photos_flow.get_first_child()
        while child:
            self.photos_flow.remove(child)
            child = self.photos_flow.get_first_child()
        self.gallery_photos = [
            item for item in items[:2_000]
            if isinstance(item, dict) and int(item.get("id", 0) or 0) > 0
        ]
        self.gallery_loaded = True
        self.gallery_cards.clear()
        self.gallery_requested_thumbnails.clear()
        self.gallery_thumbnail_cache_paths.clear()
        self.gallery_thumbnail_cache_dir = self._gallery_cache_directory()
        self.gallery_selected = None
        for photo in self.gallery_photos:
            photo_id = int(photo["id"])
            cache_path = self._gallery_thumbnail_cache_path(photo)
            if cache_path:
                self.gallery_thumbnail_cache_paths[photo_id] = cache_path
            card = Gtk.Button()
            card.add_css_class("card")
            card.set_size_request(250, 226)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            content.set_margin_top(10)
            content.set_margin_bottom(10)
            content.set_margin_start(10)
            content.set_margin_end(10)
            thumbnail = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
            thumbnail.add_css_class("gallery-thumbnail")
            thumbnail.set_pixel_size(88)
            thumbnail.set_size_request(224, 174)
            thumbnail.set_halign(Gtk.Align.CENTER)
            content.append(thumbnail)
            name = Gtk.Label(label=photo.get("name") or "Image", xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(28)
            name.add_css_class("caption")
            content.append(name)
            card.set_child(content)
            card.connect("clicked", lambda _button, record=photo: self._select_gallery_photo(record))
            self.photos_flow.append(card)
            self.gallery_cards[photo_id] = {"button": card, "image": thumbnail, "photo": photo}
        self._prune_gallery_thumbnail_cache()
        count = len(self.gallery_photos)
        self.photos_status.set_text(f"{count:,} / 2 000 photos récentes · chargement au défilement, puis cache local.".replace(",", " "))
        if count:
            self._request_photo_thumbnails(0, min(36, count))
        else:
            self.photos_status.set_text("Aucune photo accessible. Vérifie l’autorisation média sur le téléphone.")
        self._update_controls()

    def _select_gallery_photo(self, photo):
        if self.gallery_selected:
            old = self.gallery_cards.get(int(self.gallery_selected.get("id", 0)))
            if old:
                old["button"].remove_css_class("suggested-action")
        self.gallery_selected = photo
        current = self.gallery_cards.get(int(photo.get("id", 0)))
        if current:
            current["button"].add_css_class("suggested-action")
        self._update_controls()

    def _gallery_scroll_changed(self, adjustment):
        count = len(self.gallery_photos)
        if count < 37:
            return
        maximum = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        if maximum <= 0:
            return
        first = int((adjustment.get_value() / maximum) * count)
        self._request_photo_thumbnails(max(0, first - 12), min(48, count))

    def _request_photo_thumbnails(self, start, count):
        stop = min(len(self.gallery_photos), start + count)
        for photo in self.gallery_photos[start:stop]:
            photo_id = int(photo["id"])
            if photo_id in self.gallery_requested_thumbnails:
                continue
            if self._load_cached_gallery_thumbnail(photo_id):
                continue
            if not self.droidlink_bridge or not self.droidlink_bridge.snapshot()["paired"]:
                continue
            if self.droidlink_bridge.enqueue_command("photo_thumbnail", "", photoId=photo_id):
                self.gallery_requested_thumbnails.add(photo_id)

    def _gallery_cache_directory(self):
        state = self.droidlink_bridge.snapshot() if self.droidlink_bridge else {"phone": {}}
        phone = state.get("phone", {})
        identity = "|".join(
            str(phone.get(key, "")).strip()
            for key in ("manufacturer", "model", "android")
            if phone.get(key)
        )
        if not identity:
            active = self._active_device()
            identity = active[0] if active else ""
        if not identity:
            return None
        phone_key = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:20]
        return Path(GLib.get_user_cache_dir()) / "droiddesk" / "gallery" / phone_key

    def _gallery_thumbnail_cache_path(self, photo):
        if not self.gallery_thumbnail_cache_dir:
            return None
        signature = [
            photo.get("id", ""),
            photo.get("path", ""),
            photo.get("name", ""),
            photo.get("date", ""),
            photo.get("size", ""),
        ]
        digest = hashlib.sha256(
            json.dumps(signature, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.gallery_thumbnail_cache_dir / f"{digest}.jpg"

    def _prune_gallery_thumbnail_cache(self):
        directory = self.gallery_thumbnail_cache_dir
        if not directory or not directory.is_dir():
            return
        keep = {path.name for path in self.gallery_thumbnail_cache_paths.values()}
        try:
            for path in directory.glob("*.jpg"):
                if path.name not in keep:
                    path.unlink(missing_ok=True)
        except OSError:
            return

    @staticmethod
    def _read_pixbuf(data):
        loader = GdkPixbuf.PixbufLoader.new()
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()

    @staticmethod
    def _fit_pixbuf(pixbuf, width, height):
        ratio = min(width / pixbuf.get_width(), height / pixbuf.get_height())
        target_width = max(1, round(pixbuf.get_width() * ratio))
        target_height = max(1, round(pixbuf.get_height() * ratio))
        return pixbuf.scale_simple(target_width, target_height, GdkPixbuf.InterpType.HYPER)

    def _load_cached_gallery_thumbnail(self, photo_id):
        path = self.gallery_thumbnail_cache_paths.get(photo_id)
        if not path or not path.is_file():
            return False
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
        except (OSError, GLib.Error):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        card = self.gallery_cards.get(photo_id)
        if not card or not pixbuf:
            return False
        card["image"].set_from_pixbuf(self._fit_pixbuf(pixbuf, 224, 174))
        self.gallery_requested_thumbnails.add(photo_id)
        return True

    def _display_photo_thumbnail(self, event):
        try:
            photo_id = int(event.get("id", 0))
            encoded = event.get("jpeg", "")
            data = base64.b64decode(encoded, validate=True)
            pixbuf = self._read_pixbuf(data)
            card = self.gallery_cards.get(photo_id)
            if card and pixbuf:
                card["image"].set_from_pixbuf(self._fit_pixbuf(pixbuf, 224, 174))
            path = self.gallery_thumbnail_cache_paths.get(photo_id)
            if path and pixbuf:
                self._write_cached_bytes(path, data)
                self.gallery_requested_thumbnails.add(photo_id)
        except (ValueError, TypeError, GLib.Error):
            return

    def _selected_gallery_photo(self):
        photo = self.gallery_selected
        return photo if photo and self._safe_media_path(photo.get("path", "")) else None

    def _open_selected_gallery_photo(self, *_args):
        photo = self._selected_gallery_photo()
        if photo:
            self._preview_remote_image(self._safe_media_path(photo["path"]))

    def _download_selected_gallery_photo(self, *_args):
        photo = self._selected_gallery_photo()
        if not photo:
            return
        chooser = Gtk.FileChooserNative.new(
            "Télécharger cette photo",
            self,
            Gtk.FileChooserAction.SELECT_FOLDER,
            "Télécharger",
            "Annuler",
        )

        def response(dialog, answer):
            if answer == Gtk.ResponseType.ACCEPT:
                folder = dialog.get_file()
                if folder and folder.get_path():
                    self._download_file(self._safe_media_path(photo["path"]), folder.get_path())
            dialog.destroy()

        chooser.connect("response", response)
        chooser.show()

    def _open_selected_photo_externally(self, *_args):
        photo = self._selected_gallery_photo()
        active = self._active_device()
        if not photo or not active:
            return
        cache = tempfile.TemporaryDirectory(prefix="droiddesk-photo-open-")
        local_path = Path(cache.name) / Path(photo.get("name") or f"photo-{photo['id']}.jpg").name
        self.external_photo_dirs.append(cache)
        self._run_adb_command(
            ["pull", self._safe_media_path(photo["path"]), str(local_path)],
            f"Préparation de {local_path.name}…",
            lambda result, error: self._launch_external_photo(cache, local_path, result, error),
            timeout=90,
        )

    def _launch_external_photo(self, cache, local_path, result, error):
        if error or not result or result.returncode != 0 or not local_path.is_file():
            if cache in self.external_photo_dirs:
                self.external_photo_dirs.remove(cache)
            cache.cleanup()
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Impossible de transférer la photo : {detail}", True)
            return
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(local_path)).get_uri(), None)
            self._set_status(f"Ouverture de {local_path.name} dans l’application associée.")
        except GLib.Error as error:
            self._set_status(f"Aucune application ne peut ouvrir cette image : {error.message}", True)

    def _preview_remote_image(self, remote_path):
        if self.photo_preview_window:
            self.photo_preview_window.close()
            self.photo_preview_window = None
        if self.photo_preview_dir:
            self.photo_preview_dir.cleanup()
        preview_dir = tempfile.TemporaryDirectory(prefix="droiddesk-photo-")
        local_path = Path(preview_dir.name) / posixpath.basename(remote_path)
        self.photo_preview_dir = preview_dir
        self._run_adb_command(
            ["pull", remote_path, str(local_path)],
            f"Chargement de {posixpath.basename(remote_path)}…",
            lambda result, error: self._show_photo_preview(preview_dir, local_path, result, error),
            timeout=90,
        )

    def _show_photo_preview(self, preview_dir, local_path, result, error):
        if error or not result or result.returncode != 0 or not local_path.is_file():
            preview_dir.cleanup()
            if self.photo_preview_dir is preview_dir:
                self.photo_preview_dir = None
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Impossible d’afficher la photo : {detail}", True)
            return
        picture = Gtk.Picture.new_for_filename(str(local_path))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.COPY)

        def prepare_drag(*_args):
            uri_list = Gio.File.new_for_path(str(local_path)).get_uri() + "\r\n"
            payload = GLib.Bytes.new(uri_list.encode("utf-8"))
            return Gdk.ContentProvider.new_for_bytes("text/uri-list", payload)

        drag_source.connect("prepare", prepare_drag)
        picture.add_controller(drag_source)
        viewer = Gtk.ScrolledWindow()
        viewer.set_child(picture)
        window = Gtk.Window(transient_for=self)
        window.set_title(local_path.name)
        window.set_default_size(900, 700)
        window.set_child(viewer)

        def closed(*_args):
            preview_dir.cleanup()
            if self.photo_preview_dir is preview_dir:
                self.photo_preview_dir = None
            if self.photo_preview_window is window:
                self.photo_preview_window = None
            return False

        window.connect("close-request", closed)
        self.photo_preview_window = window
        window.present()
        self._set_status(f"Aperçu photo : {local_path.name}")

    def _go_parent(self, *_args):
        if self.remote_dir != "/sdcard":
            self.remote_dir = posixpath.dirname(self.remote_dir.rstrip("/")) or "/sdcard"
            if not self.remote_dir.startswith("/sdcard"):
                self.remote_dir = "/sdcard"
            self._load_directory()

    def _choose_upload(self, *_args):
        chooser = Gtk.FileChooserNative.new(
            "Envoyer un fichier au téléphone",
            self,
            Gtk.FileChooserAction.OPEN,
            "Envoyer",
            "Annuler",
        )

        def response(dialog, answer):
            if answer == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file and file.get_path():
                    self._upload_file(file.get_path())
            dialog.destroy()

        chooser.connect("response", response)
        chooser.show()

    def _upload_file(self, local_path):
        try:
            remote_dir = self._remote_path(self.remote_dir)
        except ValueError as error:
            self._set_status(str(error), True)
            return
        remote_target = posixpath.join(remote_dir, Path(local_path).name)
        self._run_adb_command(
            ["push", local_path, remote_target],
            f"Envoi de {Path(local_path).name}…",
            lambda result, error: self._after_transfer(result, error, "Fichier envoyé."),
            timeout=90,
        )

    def _choose_download_folder(self, *_args):
        remote_file = self._selected_remote_file()
        if not remote_file:
            return
        chooser = Gtk.FileChooserNative.new(
            "Choisir un dossier sur Debian",
            self,
            Gtk.FileChooserAction.SELECT_FOLDER,
            "Télécharger ici",
            "Annuler",
        )

        def response(dialog, answer):
            if answer == Gtk.ResponseType.ACCEPT:
                folder = dialog.get_file()
                if folder and folder.get_path():
                    self._download_file(remote_file, folder.get_path())
            dialog.destroy()

        chooser.connect("response", response)
        chooser.show()

    def _download_file(self, remote_file, local_dir):
        self._run_adb_command(
            ["pull", remote_file, local_dir],
            f"Téléchargement de {posixpath.basename(remote_file)}…",
            lambda result, error: self._after_transfer(result, error, "Fichier téléchargé."),
            timeout=90,
        )

    def _after_transfer(self, result, error, success):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self._set_status(f"Transfert impossible : {detail}", True)
        else:
            self._set_status(success)
            self._load_directory()

    def _new_directory(self, *_args):
        dialog = Gtk.Dialog(title="Créer un dossier", transient_for=self, modal=True)
        dialog.set_default_size(380, -1)
        content = dialog.get_content_area()
        content.set_margin_top(16)
        content.set_margin_bottom(12)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_spacing(8)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Nom du dossier")
        content.append(entry)
        dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
        dialog.add_button("Créer", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)

        def response(current, answer):
            current.destroy()
            name = entry.get_text().strip()
            if answer != Gtk.ResponseType.ACCEPT:
                return
            if not name or name in (".", "..") or "/" in name:
                self._set_status("Nom de dossier invalide.", True)
                return
            target = self._remote_path(posixpath.join(self.remote_dir, name))
            command = "mkdir " + shlex.quote(target)
            self._run_adb_command(
                ["shell", command],
                "Création du dossier…",
                lambda result, error: self._after_transfer(result, error, "Dossier créé."),
            )

        dialog.connect("response", response)
        dialog.present()

    def _show_about(self, *_args):
        about = Adw.AboutWindow(transient_for=self)
        about.set_application_name("DroidDesk")
        about.set_application_icon(APP_ID)
        about.set_version("0.3.0")
        about.set_developer_name("DroidDesk contributors")
        about.set_comments("Téléphone Android, scrcpy et transferts ADB depuis GNOME.")
        about.set_license_type(Gtk.License.GPL_3_0_ONLY)
        about.present()


class DroidDeskApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._notification_bus = None
        self._notification_signal_ids = []
        self._android_notification_ids = {}
        self._android_notification_keys = {}
        self._notification_action_ids = set()
        self.connect("activate", self._activate)
        open_notification = Gio.SimpleAction.new(
            "open-android-notification", GLib.VariantType.new("s")
        )
        open_notification.connect("activate", self._open_android_notification)
        self.add_action(open_notification)
        reply_notification = Gio.SimpleAction.new(
            "reply-android-notification", GLib.VariantType.new("s")
        )
        reply_notification.connect("activate", self._reply_android_notification)
        self.add_action(reply_notification)
        answer_call = Gio.SimpleAction.new("answer-phone-call", None)
        answer_call.connect("activate", lambda *_args: getattr(self, "window", None) and self.window._answer_call())
        self.add_action(answer_call)
        reject_call = Gio.SimpleAction.new("reject-phone-call", None)
        reject_call.connect("activate", lambda *_args: getattr(self, "window", None) and self.window._reject_call())
        self.add_action(reject_call)

    def _ensure_notification_bus(self):
        if self._notification_bus:
            return self._notification_bus
        try:
            self._notification_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._notification_signal_ids.append(
                self._notification_bus.signal_subscribe(
                    "org.freedesktop.Notifications",
                    "org.freedesktop.Notifications",
                    "NotificationClosed",
                    "/org/freedesktop/Notifications",
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._android_notification_closed,
                )
            )
            self._notification_signal_ids.append(
                self._notification_bus.signal_subscribe(
                    "org.freedesktop.Notifications",
                    "org.freedesktop.Notifications",
                    "ActionInvoked",
                    "/org/freedesktop/Notifications",
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._android_notification_action,
                )
            )
        except GLib.Error:
            self._notification_bus = None
        return self._notification_bus

    def send_android_notification(self, event):
        connection = self._ensure_notification_bus()
        if not connection:
            if hasattr(self, "window"):
                self.window._send_gnome_notification_fallback(event)
            return

        key = str(event.get("key", ""))
        title = event.get("title") or event.get("app") or "Notification Android"
        body = event.get("text", "")
        messages = event.get("messages", [])
        if messages:
            latest = messages[-1]
            snippet = latest.get("text", "")
            sender = latest.get("sender", "")
            if snippet:
                body = f"{sender}: {snippet}" if sender else snippet
        if event.get("isIncomingCall"):
            title = f"Appel entrant · {title}"

        actions = []
        if key:
            actions.extend(("default", "Ouvrir sur le téléphone"))
            if event.get("isIncomingCall"):
                actions.extend(("answer", "Répondre", "reject", "Refuser"))
            else:
                phone_actions = event.get("actions", [])
                reply = next((item for item in phone_actions if item.get("remoteInputKey")), None)
                if reply:
                    actions.extend(("reply_android", reply.get("title") or "Répondre"))
                for item in phone_actions[:3]:
                    if item is reply:
                        continue
                    try:
                        action_index = int(item.get("index", -1))
                    except (TypeError, ValueError):
                        continue
                    if action_index >= 0:
                        actions.extend((f"phone_action_{action_index}", item.get("title") or "Action"))

        hints = {
            "desktop-entry": GLib.Variant("s", APP_ID),
            "category": GLib.Variant("s", "phone.incoming-call" if event.get("isIncomingCall") else "im.received"),
            "urgency": GLib.Variant("y", 2 if event.get("isIncomingCall") else 1),
        }
        if event.get("ongoing"):
            hints["resident"] = GLib.Variant("b", True)
        replaces_id = self._android_notification_ids.get(key, 0) if key else 0
        parameters = GLib.Variant(
            "(susssasa{sv}i)",
            (
                "DroidDesk",
                replaces_id,
                "phone-symbolic",
                str(title)[:180],
                str(body)[:800],
                actions,
                hints,
                -1,
            ),
        )
        connection.call(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            parameters,
            GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            self._android_notification_sent,
            (key, event),
        )

    def _android_notification_sent(self, connection, result, user_data):
        key, event = user_data
        try:
            notification_id = connection.call_finish(result).unpack()[0]
        except GLib.Error:
            if hasattr(self, "window"):
                self.window._send_gnome_notification_fallback(event)
            return
        if not key:
            return
        old_id = self._android_notification_ids.get(key)
        if old_id and old_id != notification_id:
            self._android_notification_keys.pop(old_id, None)
        self._android_notification_ids[key] = notification_id
        self._android_notification_keys[notification_id] = key

    def close_android_notification(self, key):
        key = str(key or "")
        notification_id = self._android_notification_ids.pop(key, None)
        if not notification_id:
            return
        self._android_notification_keys.pop(notification_id, None)
        connection = self._ensure_notification_bus()
        if connection:
            connection.call(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "CloseNotification",
                GLib.Variant("(u)", (notification_id,)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
                None,
                None,
            )
        self.withdraw_notification(f"android-{key}")

    def _android_notification_closed(self, _connection, _sender, _path, _interface, _signal, parameters, _user_data=None):
        notification_id, reason = parameters.unpack()
        key = self._android_notification_keys.pop(notification_id, None)
        if not key:
            return
        self._android_notification_ids.pop(key, None)
        activated = notification_id in self._notification_action_ids
        self._notification_action_ids.discard(notification_id)
        # Freedesktop notification reason 2 means the user dismissed it.
        if reason == 2 and not activated and hasattr(self, "window"):
            self.window._send_droidlink_command("dismiss", key)

    def _android_notification_action(self, _connection, _sender, _path, _interface, _signal, parameters, _user_data=None):
        notification_id, action = parameters.unpack()
        key = self._android_notification_keys.get(notification_id)
        if not key or not hasattr(self, "window"):
            return
        self._notification_action_ids.add(notification_id)
        GLib.timeout_add_seconds(4, lambda: (self._notification_action_ids.discard(notification_id), GLib.SOURCE_REMOVE)[1])
        window = self.window
        event = next((item for item in window.droidlink_messages if item.get("key") == key), {})
        if action == "default":
            self._open_android_notification(None, GLib.Variant("s", key))
        elif action == "answer":
            window._answer_call()
        elif action == "reject":
            window._reject_call()
        elif action == "reply_android":
            phone_action = next((item for item in event.get("actions", []) if item.get("remoteInputKey")), None)
            if phone_action:
                target = {
                    "key": key,
                    "index": phone_action.get("index", -1),
                    "remoteInputKey": phone_action.get("remoteInputKey", ""),
                    "title": phone_action.get("title") or "Répondre",
                }
                self._reply_android_notification(None, GLib.Variant("s", json.dumps(target, ensure_ascii=False)))
        elif action.startswith("phone_action_"):
            try:
                action_index = int(action.rsplit("_", 1)[-1])
            except ValueError:
                return
            phone_action = next((item for item in event.get("actions", []) if item.get("index") == action_index), None)
            if phone_action:
                window._send_droidlink_notification_action(
                    key, action_index, action_title=phone_action.get("title", "")
                )

    def _open_android_notification(self, _action, target):
        key = target.get_string() if target else ""
        if not hasattr(self, "window"):
            return
        event = next(
            (item for item in self.window.droidlink_messages if item.get("key") == key),
            {},
        )
        self.window.stack.set_visible_child_name("droidlink")
        self.window.present()
        if key:
            self.window._send_droidlink_command("open", key)

    def _reply_android_notification(self, _action, target):
        if not hasattr(self, "window") or not target:
            return
        try:
            details = json.loads(target.get_string())
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        window = self.window
        window.present()
        dialog = Gtk.Dialog(
            title="Répondre à la notification",
            transient_for=window,
            modal=True,
        )
        content = dialog.get_content_area()
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(16)
        content.set_margin_end(16)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Votre réponse")
        entry.set_activates_default(True)
        content.append(entry)
        dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
        dialog.add_button("Envoyer", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)

        def response(current, answer):
            text = entry.get_text().strip()
            current.destroy()
            if answer == Gtk.ResponseType.ACCEPT and text:
                window._send_droidlink_notification_action(
                    details.get("key", ""),
                    int(details.get("index", -1)),
                    details.get("remoteInputKey", ""),
                    text,
                    details.get("title") or "Répondre",
                )

        dialog.connect("response", response)
        dialog.present()

    def _activate(self, _application):
        self._ensure_notification_bus()
        if not hasattr(self, "window"):
            self.window = DroidDeskWindow(self)
        self.window.present()


def main():
    Adw.init()
    return DroidDeskApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
