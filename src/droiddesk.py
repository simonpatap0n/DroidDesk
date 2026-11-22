#!/usr/bin/env python3
"""GNOME control panel for Android phones using ADB and scrcpy."""

import base64
import json
import posixpath
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
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
ADB_CANDIDATES = (
    Path.home() / ".local/bin/adb",
    Path.home() / ".local/share/android-sdk/platform-tools/adb",
    Path.home() / "Android/Sdk/platform-tools/adb",
    Path("/usr/bin/adb"),
    Path("/usr/local/bin/adb"),
)
SCRCPY_CANDIDATES = (Path("/usr/bin/scrcpy"), Path("/usr/local/bin/scrcpy"))
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
        fields = list(re.finditer(r"(?:^|, )([A-Za-z0-9_]+)=", text))
        item = {}
        for index, field in enumerate(fields):
            start = field.end()
            end = fields[index + 1].start() if index + 1 < len(fields) else len(text)
            item[field.group(1)] = text[start:end].strip().strip("'")
        rows.append(item)
    return rows


def _error_text(error):
    return getattr(error, "message", str(error))


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
        self.set_default_size(1080, 760)
        self.adb = find_executable("adb", ADB_CANDIDATES)
        self.scrcpy = find_executable("scrcpy", SCRCPY_CANDIDATES)
        self.devices = []
        self.device_profiles = {}
        self.contacts = []
        self.recents = []
        self.app_packages = []
        self.app_icons = {}
        self.app_labels = {}
        self.app_search_text = ""
        self.phone_profile_loading = set()
        self.scrcpy_processes = {}
        self.remote_dir = "/sdcard"
        self.file_records = {}
        self.bluetooth_busy = set()
        self.selected_adapter = None
        self.adapter_paths = []
        self._updating_bluetooth_controls = False
        self.droidlink_pinned, self.droidlink_messages = self._load_droidlink_pins()
        self.sms_notifications = []
        self.droidlink_call_state = {"state": "IDLE", "audioRoute": "none", "muted": False}
        self.incoming_call_dialog = None
        self._updating_call_audio_controls = False
        self.photo_preview_dir = None
        self.photo_preview_window = None
        self.external_photo_dirs = []
        self.gallery_photos = []
        self.gallery_cards = {}
        self.gallery_requested_thumbnails = set()
        self.gallery_selected = None
        self.busy = False
        self._build_ui()
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
        profile_labels.append(self.phone_name_label)
        profile_labels.append(self.phone_subtitle_label)
        phone_badge.append(profile_labels)
        header.pack_start(phone_badge)

        self.device_picker = Gtk.DropDown.new_from_strings(["Recherche du téléphone…"])
        self.device_model = self.device_picker.get_model()
        self.device_picker.set_tooltip_text("Téléphone Android connecté à ADB")
        self.device_picker.set_size_request(250, -1)
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
        self.stack_sidebar = Gtk.StackSidebar()
        self.stack_sidebar.set_stack(self.stack)
        self.stack_sidebar.set_size_request(190, -1)
        self.stack_sidebar.set_margin_top(12)
        self.stack_sidebar.set_margin_bottom(12)
        self.stack_sidebar.set_margin_start(10)
        self.stack_sidebar.set_margin_end(8)
        content.append(self.stack_sidebar)
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
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        root.append(self.status_label)
        toolbar.set_content(root)
        self.set_content(toolbar)

        self.calls_page = self._build_calls_page()
        self.contacts_page = self._build_contacts_page()
        self.recents_page = self._build_recents_page()
        self.sms_page = self._build_sms_page()
        self.screen_page = self._build_screen_page()
        self.files_page = self._build_files_page()
        self.bluetooth_page = self._build_bluetooth_page()
        self.adb_page = self._build_adb_page()
        self.info_page = self._build_info_page()
        self.droidlink_page = self._build_droidlink_page()
        self._add_stack_page(self.calls_page, "calls", "Appels")
        self._add_stack_page(self.contacts_page, "contacts", "Contacts")
        self._add_stack_page(self.recents_page, "recents", "Récents")
        self._add_stack_page(self.sms_page, "sms", "SMS")
        self._add_stack_page(self.screen_page, "screen", "Écran et apps")
        self._add_stack_page(self.files_page, "files", "Fichiers")
        self._add_stack_page(self.bluetooth_page, "bluetooth", "Bluetooth")
        self._add_stack_page(self.adb_page, "adb", "ADB")
        self._add_stack_page(self.info_page, "info", "Appareil")
        self._add_stack_page(self.droidlink_page, "droidlink", "DroidLink")
        self.stack.connect("notify::visible-child-name", self._on_page_changed)

    def _build_droidlink_page(self):
        page = self._page()
        page.append(self._heading(
            "DroidLink",
            "Le compagnon Android envoie les notifications dans GNOME et relie les appels, les photos et les icônes au bureau.",
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
        self.droidlink_empty = Gtk.Label(label="Aucune notification reçue.", xalign=0)
        self.droidlink_empty.add_css_class("dim-label")
        page.append(self.droidlink_empty)
        self.droidlink_list = Gtk.ListBox()
        self.droidlink_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.droidlink_list.add_css_class("boxed-list")
        page.append(self.droidlink_list)

        privacy = Gtk.Label(
            label="L’accès aux notifications maintient le canal local utilisé pour les commandes du bureau. Les images et le rôle Téléphone demandent leurs autorisations Android dédiées. Les notifications épinglées sont conservées localement; aucune connexion cloud n’est utilisée.",
            xalign=0,
        )
        privacy.set_wrap(True)
        privacy.add_css_class("dim-label")
        page.append(privacy)
        self._render_droidlink_messages()
        return page

    def _add_stack_page(self, child, name, title):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(child)
        self.stack.add_titled(scroll, name, title)

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
        title = Gtk.Label(label=text, xalign=0)
        title.add_css_class("title-1")
        box.append(title)
        if description:
            subtitle = Gtk.Label(label=description, xalign=0)
            subtitle.set_wrap(True)
            subtitle.add_css_class("dim-label")
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
        page.append(self._heading("Contacts", "Les numéros sont lus sur le téléphone et restent dans cette session."))
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.contact_search = Gtk.SearchEntry()
        self.contact_search.set_hexpand(True)
        self.contact_search.set_placeholder_text("Rechercher un nom ou un numéro")
        self.contact_search.connect("search-changed", lambda *_: self._render_contacts())
        bar.append(self.contact_search)
        self.contacts_refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.contacts_refresh_button.set_tooltip_text("Actualiser les contacts")
        self.contacts_refresh_button.connect("clicked", self._refresh_contacts)
        bar.append(self.contacts_refresh_button)
        page.append(bar)
        self.contacts_status = Gtk.Label(label="Connectez un téléphone ADB pour charger les contacts.", xalign=0)
        self.contacts_status.add_css_class("dim-label")
        page.append(self.contacts_status)
        self.contact_list = Gtk.ListBox()
        self.contact_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.contact_list.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self.contact_list)
        page.append(scroll)
        return page

    def _build_sms_page(self):
        page = self._page()
        page.append(self._heading("SMS", "Messages récents exposés par les notifications Android et composition sur le téléphone."))
        recent = Adw.PreferencesGroup.new()
        recent.set_title("Conversations récentes")
        recent.set_description("Android fournit ici les messages visibles dans les notifications actives; l’historique complet reste à intégrer.")
        self.sms_notifications_empty = Gtk.Label(label="Aucune conversation SMS disponible via les notifications.", xalign=0)
        self.sms_notifications_empty.add_css_class("dim-label")
        recent.add(self.sms_notifications_empty)
        self.sms_notification_list = Gtk.ListBox()
        self.sms_notification_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.sms_notification_list.add_css_class("boxed-list")
        recent.add(self.sms_notification_list)
        page.append(recent)
        compose = Adw.PreferencesGroup.new()
        compose.set_title("Nouveau message")
        compose.set_description("Un numéro ou plusieurs destinataires séparés par des virgules; Messages Android confirme l’envoi ou le groupe.")
        self.sms_number_entry = Gtk.Entry()
        self.sms_number_entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        self.sms_number_entry.set_placeholder_text("Numéro(s), séparés par des virgules")
        number_row = Adw.ActionRow.new()
        number_row.set_title("À")
        number_row.add_suffix(self.sms_number_entry)
        compose.add(number_row)
        self.sms_body_view = Gtk.TextView()
        self.sms_body_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.sms_body_view.set_left_margin(12)
        self.sms_body_view.set_right_margin(12)
        self.sms_body_view.set_top_margin(10)
        self.sms_body_view.set_bottom_margin(10)
        self.sms_body_view.set_size_request(-1, 150)
        body_scroll = Gtk.ScrolledWindow()
        body_scroll.set_min_content_height(150)
        body_scroll.set_child(self.sms_body_view)
        compose.add(body_scroll)
        self.sms_open_button = Gtk.Button(label="Ouvrir dans Messages")
        self.sms_open_button.add_css_class("suggested-action")
        self.sms_open_button.connect("clicked", self._send_sms_to_phone)
        send_row = Adw.ActionRow.new()
        send_row.set_title("Continuer sur le téléphone")
        send_row.add_suffix(self.sms_open_button)
        compose.add(send_row)
        page.append(compose)
        privacy = Gtk.Label(
            label="Les réponses rapides utilisent l’action fournie par l’application SMS Android. Les nouveaux messages et groupes sont préparés dans Messages; la lecture complète et l’envoi direct de photos/GIF restent à intégrer.",
            xalign=0,
        )
        privacy.set_wrap(True)
        privacy.add_css_class("dim-label")
        page.append(privacy)
        return page

    def _build_recents_page(self):
        page = self._page()
        page.append(self._heading("Appels récents", "Rappeler un numéro à partir du journal Android."))
        recent_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.recents_status = Gtk.Label(label="Connectez un téléphone ADB pour charger les appels.", xalign=0)
        self.recents_status.set_hexpand(True)
        self.recents_status.add_css_class("dim-label")
        recent_actions.append(self.recents_status)
        self.recents_refresh_button = Gtk.Button(label="Actualiser")
        self.recents_refresh_button.connect("clicked", self._refresh_recents)
        recent_actions.append(self.recents_refresh_button)
        self.recents_open_button = Gtk.Button(label="Ouvrir sur Android")
        self.recents_open_button.connect("clicked", self._open_android_call_log)
        recent_actions.append(self.recents_open_button)
        page.append(recent_actions)
        self.recent_list = Gtk.ListBox()
        self.recent_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recent_list.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self.recent_list)
        page.append(scroll)
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

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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

        key_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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
        output_scroll.set_min_content_height(170)
        output_scroll.set_max_content_height(300)
        output_scroll.set_child(self.adb_output)
        server_group.add(output_scroll)
        page.append(server_group)

        diagnostics = Adw.PreferencesGroup.new()
        diagnostics.set_title("Diagnostics")
        diagnostic_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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
        page.append(self._heading("Appels", "Composer sur le téléphone et gérer les appels depuis Debian."))

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
        dialpad_row = Adw.ActionRow.new()
        dialpad_row.set_title("Pavé numérique")
        dialpad_row.add_suffix(self.dialpad)
        outgoing.add(dialpad_row)
        self.dial_button = Gtk.Button(label="Ouvrir le composeur")
        self.dial_button.add_css_class("suggested-action")
        self.dial_button.connect("clicked", self._place_call)
        dial_row = Adw.ActionRow.new()
        dial_row.set_title("Composer sur le téléphone")
        dial_row.add_suffix(self.dial_button)
        outgoing.add(dial_row)
        page.append(outgoing)

        incoming = Adw.PreferencesGroup.new()
        incoming.set_title("Appel en cours ou entrant")
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.answer_button = Gtk.Button(label="Répondre")
        self.answer_button.connect("clicked", self._answer_call)
        self.reject_call_button = Gtk.Button(label="Refuser")
        self.reject_call_button.add_css_class("destructive-action")
        self.reject_call_button.connect("clicked", self._reject_call)
        self.hangup_button = Gtk.Button(label="Terminer l’appel")
        self.hangup_button.add_css_class("destructive-action")
        self.hangup_button.connect("clicked", self._hangup_call)
        controls.append(self.answer_button)
        controls.append(self.reject_call_button)
        controls.append(self.hangup_button)
        control_row = Adw.ActionRow.new()
        control_row.set_title("Commandes")
        control_row.add_suffix(controls)
        incoming.add(control_row)
        self.call_link_status = Gtk.Label(label="Aucun appel DroidLink en cours.", xalign=0)
        self.call_link_status.set_wrap(True)
        self.call_link_status.add_css_class("dim-label")
        incoming.add(self.call_link_status)
        audio_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.call_audio_bluetooth_button = Gtk.Button(label="Audio PC · Bluetooth")
        self.call_audio_bluetooth_button.set_tooltip_text("Router l’appel vers le profil mains libres Bluetooth du PC")
        self.call_audio_bluetooth_button.connect("clicked", lambda *_: self._set_call_audio_route("bluetooth"))
        self.call_audio_speaker_button = Gtk.Button(label="Haut-parleur téléphone")
        self.call_audio_speaker_button.connect("clicked", lambda *_: self._set_call_audio_route("speaker"))
        self.call_mute_button = Gtk.ToggleButton(label="Microphone muet")
        self.call_mute_button.connect("toggled", self._set_call_muted)
        audio_controls.append(self.call_audio_bluetooth_button)
        audio_controls.append(self.call_audio_speaker_button)
        audio_controls.append(self.call_mute_button)
        audio_row = Adw.ActionRow.new()
        audio_row.set_title("Audio de l’appel")
        audio_row.add_suffix(audio_controls)
        incoming.add(audio_row)
        audio_hint = Gtk.Label(
            label="Le son du PC passe par Bluetooth mains libres si Debian est appairé comme casque et si Android propose cette route.",
            xalign=0,
        )
        audio_hint.set_wrap(True)
        audio_hint.add_css_class("dim-label")
        incoming.add(audio_hint)
        page.append(incoming)

        quick_links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.contacts_quick_button = Gtk.Button(label="Ouvrir les contacts")
        self.contacts_quick_button.connect("clicked", lambda *_: self.stack.set_visible_child_name("contacts"))
        self.recents_quick_button = Gtk.Button(label="Appels récents")
        self.recents_quick_button.connect("clicked", lambda *_: self.stack.set_visible_child_name("recents"))
        quick_links.append(self.contacts_quick_button)
        quick_links.append(self.recents_quick_button)
        page.append(quick_links)

        note = Gtk.Label(
            label=(
                "Pour les appels intégrés, choisis DroidLink comme application Téléphone par défaut sur Android. Les appels utilisent la SIM du téléphone; "
                "garde aussi l’accès aux notifications actif dans DroidLink pour recevoir les commandes du bureau. Répondre, refuser, terminer, sélectionner la route audio et couper le micro sont alors pilotables depuis Debian. Le son du PC utilise "
                "le profil Bluetooth mains libres si le téléphone propose cette route et si Debian est connecté comme casque. Sinon, utilise le haut-parleur ou l’écouteur du téléphone."
            ),
            xalign=0,
        )
        note.set_wrap(True)
        note.add_css_class("card")
        note.set_margin_top(8)
        note.set_margin_bottom(8)
        note.set_margin_start(12)
        note.set_margin_end(12)
        page.append(note)
        return page

    def _build_screen_page(self):
        page = self._page()
        page.append(self._heading("Écran et applications", "Ouvrir le téléphone avec scrcpy ou lancer et gérer ses applications."))

        screen_group = Adw.PreferencesGroup.new()
        screen_group.set_title("Contrôle de l’écran")
        screen_group.set_description("scrcpy ouvre l’écran Android et permet d’utiliser la souris et le clavier.")
        self.scrcpy_button = Gtk.Button(label="Ouvrir scrcpy · contrôle complet")
        self.scrcpy_button.add_css_class("suggested-action")
        self.scrcpy_button.connect("clicked", self._start_scrcpy)
        screen_row = Adw.ActionRow.new()
        screen_row.set_title("scrcpy")
        screen_row.add_suffix(self.scrcpy_button)
        screen_group.add(screen_row)
        page.append(screen_group)

        apps_group = Adw.PreferencesGroup.new()
        apps_group.set_title("Applications installées")
        apps_group.set_description("Clic gauche pour ouvrir · clic droit pour désinstaller ou afficher les informations.")
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
        apps_group.add(apps_row)
        self.apps_flow = Gtk.FlowBox()
        self.apps_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.apps_flow.set_homogeneous(True)
        self.apps_flow.set_max_children_per_line(5)
        self.apps_flow.set_min_children_per_line(2)
        self.apps_flow.set_row_spacing(10)
        self.apps_flow.set_column_spacing(10)
        self.apps_flow.set_valign(Gtk.Align.START)
        apps_scroll = Gtk.ScrolledWindow()
        apps_scroll.set_min_content_height(420)
        apps_scroll.set_max_content_height(600)
        apps_scroll.set_child(self.apps_flow)
        apps_group.add(apps_scroll)
        page.append(apps_group)

        install_group = Adw.PreferencesGroup.new()
        install_group.set_title("Installer une application")
        install_group.set_description("Choisissez un fichier APK pour l’installer sur le téléphone.")
        self.install_apk_button = Gtk.Button(label="Choisir un APK…")
        self.install_apk_button.connect("clicked", self._choose_apk)
        install_row = Adw.ActionRow.new()
        install_row.set_title("Fichier APK")
        install_row.add_suffix(self.install_apk_button)
        install_group.add(install_row)
        page.append(install_group)
        return page

    def _build_files_page(self):
        page = self._page()
        page.append(self._heading("Fichiers", "Parcourez le stockage partagé du téléphone et transférez des fichiers."))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.up_button = Gtk.Button.new_from_icon_name("go-up-symbolic")
        self.up_button.set_tooltip_text("Dossier parent")
        self.up_button.connect("clicked", self._go_parent)
        bar.append(self.up_button)
        self.remote_path_label = Gtk.Label(label=self.remote_dir, xalign=0)
        self.remote_path_label.set_hexpand(True)
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
        file_scroll.set_min_content_height(330)
        file_scroll.set_vexpand(True)
        file_scroll.set_child(self.file_list)
        page.append(file_scroll)

        transfer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.upload_button = Gtk.Button(label="Envoyer vers le téléphone…")
        self.upload_button.connect("clicked", self._choose_upload)
        self.download_button = Gtk.Button(label="Télécharger vers Debian…")
        self.download_button.connect("clicked", self._choose_download_folder)
        transfer.append(self.upload_button)
        transfer.append(self.download_button)
        page.append(transfer)

        gallery_group = Adw.PreferencesGroup.new()
        gallery_group.set_title("Photos récentes")
        gallery_group.set_description("Jusqu’à 2 000 photos indexées par Android. Les vignettes arrivent au fil de la navigation.")
        gallery_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.photos_gallery_button = Gtk.Button(label="Charger les photos")
        self.photos_gallery_button.connect("clicked", self._load_photos_gallery)
        gallery_controls.append(self.photos_gallery_button)
        self.photos_open_button = Gtk.Button(label="Aperçu")
        self.photos_open_button.connect("clicked", self._open_selected_gallery_photo)
        gallery_controls.append(self.photos_open_button)
        self.photos_download_button = Gtk.Button(label="Télécharger…")
        self.photos_download_button.connect("clicked", self._download_selected_gallery_photo)
        gallery_controls.append(self.photos_download_button)
        self.photos_external_button = Gtk.Button(label="Ouvrir dans une application")
        self.photos_external_button.connect("clicked", self._open_selected_photo_externally)
        gallery_controls.append(self.photos_external_button)
        gallery_group.add(gallery_controls)
        self.photos_status = Gtk.Label(label="Autorise les photos dans DroidLink → Accès, puis charge la galerie.", xalign=0)
        self.photos_status.set_wrap(True)
        self.photos_status.add_css_class("dim-label")
        gallery_group.add(self.photos_status)
        self.photos_flow = Gtk.FlowBox()
        self.photos_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.photos_flow.set_homogeneous(True)
        self.photos_flow.set_min_children_per_line(3)
        self.photos_flow.set_max_children_per_line(7)
        self.photos_flow.set_row_spacing(10)
        self.photos_flow.set_column_spacing(10)
        self.photos_flow.set_valign(Gtk.Align.START)
        self.photos_scroll = Gtk.ScrolledWindow()
        self.photos_scroll.set_min_content_height(360)
        self.photos_scroll.set_max_content_height(620)
        self.photos_scroll.set_child(self.photos_flow)
        self.photos_scroll.get_vadjustment().connect("value-changed", self._gallery_scroll_changed)
        gallery_group.add(self.photos_scroll)
        page.append(gallery_group)

        self.files_note = Gtk.Label(
            label="Le navigateur ouvre le stockage partagé /sdcard; les dossiers privés des applications ne sont pas accessibles sans root.",
            xalign=0,
        )
        self.files_note.set_wrap(True)
        self.files_note.add_css_class("dim-label")
        page.append(self.files_note)
        return page

    def _build_info_page(self):
        page = self._page()
        page.append(self._heading("Appareil", "État et renseignements du téléphone Android connecté."))
        info_group = Adw.PreferencesGroup.new()
        info_group.set_title("Informations")
        self.info_view = Gtk.Label(label="Appuyez sur Actualiser pour lire les informations.", xalign=0)
        self.info_view.set_selectable(True)
        self.info_view.set_wrap(True)
        self.info_view.add_css_class("monospace")
        info_row = Adw.ActionRow.new()
        info_row.set_title("Résumé système")
        info_row.set_subtitle("Modèle, version, batterie et stockage")
        info_group.add(info_row)
        page.append(info_group)
        info_frame = Gtk.Frame()
        info_frame.set_child(self.info_view)
        info_frame.set_margin_start(8)
        info_frame.set_margin_end(8)
        page.append(info_frame)
        self.info_refresh_button = Gtk.Button(label="Actualiser les informations")
        self.info_refresh_button.connect("clicked", self._refresh_info)
        page.append(self.info_refresh_button)
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
        self._set_status("Tunnel ADB prêt. Ouvrez DroidLink sur Android et saisissez le code affiché.")
        self._droidlink_state_changed()

    def _disconnect_droidlink(self, *_args):
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
            call_ready = phone.get("dialerRole") and phone.get("notificationAccess") and phone.get("callPhoneAccess")
            dialer = " · appels PC prêts" if call_ready else ""
            needs_access = " · active les notifications dans DroidLink" if not phone.get("notificationAccess") else ""
            if phone.get("dialerRole") and not phone.get("callPhoneAccess"):
                needs_access += " · autorise les appels sortants dans DroidLink → Accès"
            self.droidlink_status.set_text(f"Associé · {name or 'Téléphone Android'} · Android {phone.get('android') or '—'}{dialer}{needs_access}")
            self.droidlink_code_label.set_text("—")
        elif state["code"]:
            self.droidlink_status.set_text(f"En attente du téléphone · code valide encore {state['seconds']} s.")
            code = state["code"]
            self.droidlink_code_label.set_text(f"{code[:3]} {code[3:]}")
        else:
            self.droidlink_status.set_text("Aucun téléphone DroidLink associé.")
            self.droidlink_code_label.set_text("—")
        self.droidlink_empty.set_visible(not self.droidlink_messages)
        self._update_controls()
        return GLib.SOURCE_REMOVE

    def _droidlink_received(self, event):
        if event.get("type") == "call_command_error":
            message = event.get("message") or "Android n’a pas pu exécuter l’appel."
            self._set_status(message, True)
            return GLib.SOURCE_REMOVE
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
                        self.app_labels[package] = item.get("label", "")[:100]
                except (ValueError, GLib.Error):
                    continue
            self._render_apps()
            return GLib.SOURCE_REMOVE
        if event.get("type") == "call_log_list":
            types = {1: "Entrant", 2: "Sortant", 3: "Manqué", 4: "Messagerie vocale", 5: "Refusé", 6: "Bloqué"}
            self.recents = []
            for item in event.get("items", [])[:100]:
                number = item.get("number", "").strip() or "Numéro masqué"
                try:
                    when = datetime.fromtimestamp(int(item.get("date", 0)) / 1000).strftime("%d %b · %H:%M")
                except (TypeError, ValueError, OSError, OverflowError):
                    when = ""
                name = item.get("name", "").strip() or number
                self.recents.append((name, number, types.get(int(item.get("type", 0)), "Appel"), when))
            self._render_recents()
            self.recents_status.set_text(f"{len(self.recents)} appel(s) récent(s) chargé(s) par DroidLink.")
            self._set_status("Journal d’appels DroidLink actualisé.")
            return GLib.SOURCE_REMOVE
        if event.get("type") == "call_log_error":
            message = event.get("message") or "DroidLink ne peut pas lire le journal d’appels."
            self.recents_status.set_text(message)
            self._set_status(message, True)
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_list":
            self._populate_photos_gallery(event.get("items", []))
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_thumbnail":
            self._display_photo_thumbnail(event)
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_error":
            message = event.get("message") or "DroidLink n’a pas pu lire les photos du téléphone."
            self.photos_status.set_text(message)
            self._set_status(message, True)
            return GLib.SOURCE_REMOVE
        if event.get("type") == "photo_thumbnail_error":
            return GLib.SOURCE_REMOVE
        if event.get("type") == "call":
            state = event.get("state", "IDLE")
            self.droidlink_call_state = {
                "state": state,
                "audioRoute": event.get("audioRoute", "none"),
                "muted": bool(event.get("muted", False)),
                "supportedAudioRoutes": int(event.get("supportedAudioRoutes", 0) or 0),
            }
            who = event.get("displayName") or event.get("number") or "Numéro inconnu"
            audio = {
                "bluetooth": "Bluetooth PC",
                "speaker": "haut-parleur téléphone",
                "earpiece": "écouteur téléphone",
                "wired": "casque filaire",
            }.get(event.get("audioRoute"), "audio non sélectionné")
            if event.get("muted"):
                audio += " · micro muet"
            labels = {
                "RINGING": f"Appel entrant · {who} · {audio}",
                "DIALING": f"Appel en cours vers {who} · {audio}",
                "ACTIVE": f"En communication avec {who} · {audio}",
                "HOLDING": f"Appel en attente · {who} · {audio}",
                "DISCONNECTED": "Appel terminé.",
                "IDLE": "Aucun appel DroidLink en cours.",
            }
            route_status = labels.get(state, f"Appel · {who}")
            if state in ("RINGING", "DIALING", "ACTIVE", "HOLDING"):
                routes = self.droidlink_call_state["supportedAudioRoutes"]
                if routes & 2 and event.get("audioRoute") != "bluetooth":
                    route_status += " · profil Bluetooth mains libres disponible, en attente de sélection"
                elif not routes & 2:
                    route_status += " · Debian n’est pas détecté comme casque mains libres Bluetooth"
            self.call_link_status.set_text(route_status)
            if state == "RINGING":
                self._show_incoming_call_dialog(who, event.get("number", ""))
            elif self.incoming_call_dialog:
                self.incoming_call_dialog.close()
                self.incoming_call_dialog = None
            self._updating_call_audio_controls = True
            self.call_mute_button.set_active(bool(event.get("muted", False)))
            self.call_mute_button.set_label("Microphone muet" if event.get("muted") else "Couper le micro")
            self._updating_call_audio_controls = False
            self._update_controls()
            if state == "RINGING":
                self._set_status(f"Appel entrant de {who}.")
            elif state == "IDLE":
                self._set_status("Aucun appel DroidLink en cours.")
            else:
                self._set_status(labels.get(state, f"Appel · {who}"))
            return GLib.SOURCE_REMOVE
        key = event.get("key", "")
        if event.get("removed"):
            application = self.get_application()
            if application:
                application.withdraw_notification(f"android-{key}")
            for item in self.droidlink_messages:
                if item.get("key") == key:
                    item["active"] = False
            self.sms_notifications = [item for item in self.sms_notifications if item.get("key") != key]
            self._render_sms_notifications()
            self.droidlink_messages = [
                item for item in self.droidlink_messages
                if item.get("active", True) or item.get("key") in self.droidlink_pinned
            ]
            self._save_droidlink_pins()
            self._render_droidlink_messages()
            return GLib.SOURCE_REMOVE
        event["pinned"] = event.get("key", "") in self.droidlink_pinned
        event["active"] = True
        self._send_gnome_notification(event)
        existing = next((index for index, item in enumerate(self.droidlink_messages) if key and item.get("key") == key), None)
        if existing is None:
            self.droidlink_messages.insert(0, event)
        else:
            self.droidlink_messages[existing] = event
        if event.get("isSmsApp"):
            self.sms_notifications = [item for item in self.sms_notifications if item.get("key") != key]
            self.sms_notifications.insert(0, event)
            self.sms_notifications = self.sms_notifications[:30]
        else:
            self.sms_notifications = [item for item in self.sms_notifications if item.get("key") != key]
        self._render_sms_notifications()
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

    def _show_incoming_call_dialog(self, caller, number):
        if self.incoming_call_dialog:
            return
        dialog = Adw.MessageDialog.new(self, "Appel entrant", caller or number or "Numéro inconnu")
        if number and number != caller:
            dialog.set_body(number)
        dialog.add_response("decline", "Refuser")
        dialog.add_response("answer", "Répondre")
        dialog.set_response_appearance("decline", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("answer", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("answer")
        dialog.connect("response", self._incoming_call_response)
        self.incoming_call_dialog = dialog
        dialog.present()

    def _incoming_call_response(self, dialog, response):
        self.incoming_call_dialog = None
        if response == "answer":
            self._queue_droidlink_call("answer_call")
        elif response == "decline":
            self._queue_droidlink_call("reject_call")

    def _render_sms_notifications(self):
        if not hasattr(self, "sms_notification_list"):
            return
        child = self.sms_notification_list.get_first_child()
        while child:
            self.sms_notification_list.remove(child)
            child = self.sms_notification_list.get_first_child()
        self.sms_notifications_empty.set_visible(not self.sms_notifications)
        for event in self.sms_notifications:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            row.set_margin_start(10)
            row.set_margin_end(10)
            heading = event.get("conversationTitle") or event.get("title") or "SMS"
            title = Gtk.Label(label=heading, xalign=0)
            title.add_css_class("heading")
            row.append(title)
            messages = event.get("messages", [])
            if messages:
                for message in messages[-8:]:
                    text = message.get("text", "")
                    if not text and message.get("mediaType"):
                        text = f"Média reçu · {message['mediaType']}"
                    if not text:
                        continue
                    sender = message.get("sender", "")
                    bubble = Gtk.Label(label=f"{sender}: {text}" if sender else text, xalign=0)
                    bubble.set_wrap(True)
                    bubble.set_selectable(True)
                    bubble.add_css_class("card")
                    row.append(bubble)
            elif event.get("text"):
                body = Gtk.Label(label=event["text"], xalign=0)
                body.set_wrap(True)
                body.set_selectable(True)
                row.append(body)
            if event.get("isGroupConversation"):
                group_label = Gtk.Label(label="Conversation de groupe", xalign=0)
                group_label.add_css_class("dim-label")
                row.append(group_label)
            key = event.get("key", "")
            if key and event.get("active", True):
                for phone_action in event.get("actions", []):
                    remote_key = phone_action.get("remoteInputKey", "")
                    action_index = phone_action.get("index", -1)
                    if remote_key and action_index >= 0:
                        reply = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        entry = Gtk.Entry()
                        entry.set_hexpand(True)
                        entry.set_placeholder_text("Répondre au SMS…")
                        reply.append(entry)
                        send = Gtk.Button(label=phone_action.get("title") or "Répondre")
                        send.add_css_class("suggested-action")
                        send.connect(
                            "clicked",
                            lambda _button, field=entry, notification_key=key, index=action_index, result_key=remote_key, title=phone_action.get("title", "Répondre"):
                            self._send_droidlink_notification_action(notification_key, index, result_key, field.get_text(), title),
                        )
                        reply.append(send)
                        row.append(reply)
                open_button = Gtk.Button(label="Ouvrir la conversation sur Android")
                open_button.connect("clicked", lambda *_args, notification_key=key: self._send_droidlink_command("open", notification_key))
                row.append(open_button)
            self.sms_notification_list.append(row)

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
                archived = Gtk.Label(label="Notification épinglée · retirée du téléphone", xalign=0)
                archived.add_css_class("dim-label")
                row.append(archived)
            self.droidlink_list.append(row)
        self.droidlink_empty.set_visible(not self.droidlink_messages)

    def _send_droidlink_command(self, action, key):
        if not self.droidlink_bridge or not self.droidlink_bridge.enqueue_command(action, key):
            self._set_status("Associez DroidLink avant de contrôler les notifications.", True)
            return
        if action == "dismiss":
            if key in self.droidlink_pinned:
                for item in self.droidlink_messages:
                    if item.get("key") == key:
                        item["active"] = False
            else:
                self.droidlink_messages = [item for item in self.droidlink_messages if item.get("key") != key]
            self._save_droidlink_pins()
            self._render_droidlink_messages()
            self._set_status("La notification sera ignorée sur le téléphone au prochain échange ADB.")
        else:
            self._set_status("Ouverture de la notification demandée sur Android.")

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
                messages = [item for item in values.get("messages", []) if isinstance(item, dict) and item.get("key")]
                return {item["key"] for item in messages}, messages[:50]
        except (OSError, ValueError, TypeError):
            pass
        return set(), []

    def _save_droidlink_pins(self):
        path = self._droidlink_pins_path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            messages = [item for item in self.droidlink_messages if item.get("key") in self.droidlink_pinned]
            path.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
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
        for widget in (
            self.dial_button,
            self.answer_button,
            self.reject_call_button,
            self.hangup_button,
            self.scrcpy_button,
            self.apps_refresh_button,
            self.install_apk_button,
            self.sms_open_button,
            self.up_button,
            self.files_refresh_button,
            self.photos_dcim_button,
            self.photos_pictures_button,
            self.mkdir_button,
            self.upload_button,
            self.info_refresh_button,
            self.contacts_refresh_button,
            self.recents_refresh_button,
            self.recents_open_button,
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
        self.number_entry.set_sensitive(ready)
        self.sms_number_entry.set_sensitive(ready)
        self.sms_body_view.set_sensitive(ready)
        self.apps_search.set_sensitive(ready)
        self.adb_shell_entry.set_sensitive(ready)
        self.file_list.set_sensitive(ready)
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
        integrated_call_controls = (
            ready
            and state["paired"]
            and state["phone"].get("dialerRole")
            and state["phone"].get("notificationAccess")
            and self.droidlink_call_state.get("state") not in ("IDLE", "DISCONNECTED")
        )
        supported_routes = self.droidlink_call_state.get("supportedAudioRoutes", 0)
        self.call_audio_bluetooth_button.set_sensitive(integrated_call_controls and bool(supported_routes & 2))
        self.call_audio_speaker_button.set_sensitive(integrated_call_controls and bool(supported_routes & 8))
        self.call_mute_button.set_sensitive(integrated_call_controls)
        photo_path_available = self._selected_gallery_photo() is not None
        self.photos_gallery_button.set_sensitive(ready and state["paired"] and state["phone"].get("notificationAccess"))
        self.photos_open_button.set_sensitive(ready and photo_path_available)
        self.photos_download_button.set_sensitive(ready and photo_path_available)
        self.photos_external_button.set_sensitive(ready and photo_path_available)
        self.droidlink_disconnect_button.set_sensitive(linked and not self.busy)
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
        self._update_controls()
        self.remote_dir = "/sdcard"
        self.remote_path_label.set_text(self.remote_dir)
        self.contacts = []
        self.recents = []
        self._render_contacts()
        self._render_recents()
        if self._active_device():
            self.contacts_status.set_text("Ouvrez cette page pour charger les contacts du téléphone sélectionné.")
            self.recents_status.set_text("Ouvrez cette page pour charger le journal de ce téléphone.")
            self._refresh_device_profile()
            self._load_current_page()
        else:
            self.phone_name_label.set_text("Aucun téléphone")
            self.phone_subtitle_label.set_text("Connectez un appareil ADB")
            self.contacts_status.set_text("Connectez un téléphone ADB pour charger les contacts.")
            self.recents_status.set_text("Connectez un téléphone ADB pour charger les appels.")
            self.phone_thumb.queue_draw()

    def _on_page_changed(self, *_args):
        if self.stack.get_visible_child_name() == "droidlink":
            self._droidlink_state_changed()
        self._load_current_page()

    def _load_current_page(self):
        if not self._active_device() or self.busy:
            return
        page = self.stack.get_visible_child_name()
        if page == "screen":
            self._refresh_apps()
        elif page == "files":
            self._load_directory()
        elif page == "contacts":
            self._refresh_contacts()
        elif page == "recents":
            self._refresh_recents()
        elif page == "info":
            self._refresh_info()

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
            self._set_status("Aucun téléphone détecté. Branchez l’USB ou activez le débogage sans fil.")
        elif any(state == "unauthorized" for _serial, state, _model in devices):
            self._set_status("Autorisez le débogage USB sur l’écran du téléphone, puis actualisez.")
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

    def _refresh_device_profile(self):
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
            GLib.idle_add(self._device_profile_result, serial, output, error)

        threading.Thread(target=worker, daemon=True).start()

    def _device_profile_result(self, serial, output, error):
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
        self.phone_thumb.set_tooltip_text(f"{manufacturer} {model}".strip())
        self.phone_thumb.queue_draw()
        return GLib.SOURCE_REMOVE

    def _append_digit(self, digit):
        self.number_entry.set_text(self.number_entry.get_text() + digit)
        self.number_entry.set_position(-1)

    def _refresh_contacts(self, *_args):
        self.contacts_status.set_text("Lecture des contacts…")
        self._run_adb_command(
            ["shell", "content", "query", "--uri", "content://com.android.contacts/data/phones", "--projection", "display_name:data1"],
            "Lecture des contacts Android…",
            self._contacts_result,
        )

    def _contacts_result(self, result, error):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            self.contacts = []
            self._render_contacts()
            self.contacts_status.set_text("Accès aux contacts refusé ou fournisseur indisponible. Autorisez l’accès sur le téléphone si Android le demande.")
            self._set_status(f"Contacts indisponibles : {detail}", True)
            return
        contacts = []
        seen = set()
        for row in _content_rows(result.stdout):
            name = row.get("display_name", "").strip() or "Contact sans nom"
            number = row.get("data1", "").strip()
            key = (name.casefold(), number)
            if number and key not in seen:
                seen.add(key)
                contacts.append((name, number))
        self.contacts = sorted(contacts, key=lambda item: item[0].casefold())
        self._render_contacts()
        self.contacts_status.set_text(f"{len(self.contacts)} numéro(s) trouvé(s).")
        self._set_status(f"{len(self.contacts)} numéro(s) de contact chargés.")

    def _render_contacts(self):
        while child := self.contact_list.get_first_child():
            self.contact_list.remove(child)
        query = self.contact_search.get_text().strip().casefold()
        visible = [(name, number) for name, number in self.contacts if query in name.casefold() or query in number.casefold()]
        for name, number in visible:
            row = Adw.ActionRow.new()
            row.set_title(GLib.markup_escape_text(name))
            row.set_subtitle(number)
            row.add_prefix(Gtk.Image.new_from_icon_name("avatar-default-symbolic"))
            button = Gtk.Button.new_from_icon_name("call-start-symbolic")
            button.add_css_class("flat")
            button.set_tooltip_text("Composer ce numéro")
            button.connect("clicked", lambda *_args, value=number: self._call_number(value))
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            message_button = Gtk.Button.new_from_icon_name("mail-message-new-symbolic")
            message_button.add_css_class("flat")
            message_button.set_tooltip_text("Préparer un SMS")
            message_button.connect("clicked", lambda *_args, value=number: self._open_sms(value))
            actions.append(message_button)
            actions.append(button)
            row.add_suffix(actions)
            self.contact_list.append(row)

    def _refresh_recents(self, *_args):
        if self.droidlink_bridge:
            state = self.droidlink_bridge.snapshot()
            phone = state.get("phone", {})
            if state["paired"] and phone.get("dialerRole") and phone.get("notificationAccess") and phone.get("callLogAccess"):
                if self.droidlink_bridge.enqueue_command("call_log_list", ""):
                    self.recents_status.set_text("Lecture du journal d’appels par DroidLink…")
                    self._set_status("DroidLink lit le journal d’appels autorisé sur Android.")
                    return
            elif state["paired"] and phone.get("dialerRole") and not phone.get("callLogAccess"):
                self.recents_status.set_text("Autorise l’historique d’appels dans DroidLink → Accès, puis actualise.")
                self._set_status("Autorise la permission du journal d’appels dans DroidLink → Accès.", True)
                return
            elif state["paired"] and phone.get("dialerRole") and not phone.get("notificationAccess"):
                self.recents_status.set_text("Active le relais de notifications dans DroidLink pour synchroniser l’historique.")
                self._set_status("Active le relais de notifications pour synchroniser le journal DroidLink.", True)
                return
        self.recents_status.set_text("Lecture du journal d’appels…")
        self._run_adb_command(
            ["shell", "content", "query", "--uri", "content://call_log/calls", "--projection", "number:type:date:duration:name"],
            "Lecture des appels récents…",
            self._recents_result,
        )

    def _recents_result(self, result, error, fallback=False, previous_error=""):
        if error or not result or result.returncode != 0:
            detail = error or ((result.stderr or result.stdout).strip() if result else "")
            if not fallback:
                self._run_adb_command(
                    ["shell", "content", "query", "--uri", "content://call_log/calls", "--projection", "number:type:date:duration"],
                    "Nouvelle tentative du journal d’appels…",
                    lambda retry, retry_error: self._recents_result(retry, retry_error, True, detail),
                )
                return
            detail = "; ".join(item for item in (previous_error, detail) if item)
            self.recents = []
            self._render_recents()
            self.recents_status.set_text(f"Lecture refusée par Android : {detail[:180] or 'permission ou fournisseur indisponible'}")
            self._set_status("Le journal d’appels est inaccessible à ADB. Android protège ces données avec READ_CALL_LOG.", True)
            return
        rows = _content_rows(result.stdout)
        provider_error = re.search(r"SecurityException|Permission Denial|unknown column|Error:", result.stdout, re.IGNORECASE)
        if not rows and provider_error:
            detail = result.stdout.strip()
            if not fallback:
                self._run_adb_command(
                    ["shell", "content", "query", "--uri", "content://call_log/calls", "--projection", "number:type:date:duration"],
                    "Nouvelle tentative du journal d’appels…",
                    lambda retry, retry_error: self._recents_result(retry, retry_error, True, detail),
                )
                return
            self.recents_status.set_text(f"Lecture refusée par Android : {detail[:180]}")
            self._set_status("Android refuse l’accès au journal d’appels via ADB. READ_CALL_LOG est une permission restreinte.", True)
            return
        types = {"1": "Entrant", "2": "Sortant", "3": "Manqué", "4": "Messagerie vocale", "5": "Refusé", "6": "Bloqué"}
        self.recents = []
        for row in rows:
            number = row.get("number", "").strip() or "Numéro masqué"
            raw_date = row.get("date", "")
            try:
                when = datetime.fromtimestamp(int(raw_date) / 1000).strftime("%d %b · %H:%M")
            except (TypeError, ValueError, OSError, OverflowError):
                when = ""
            name = row.get("name", "").strip()
            if not name or name.casefold() == "null":
                name = number
            self.recents.append((name, number, types.get(row.get("type", ""), "Appel"), when))
        self._render_recents()
        self.recents_status.set_text(f"{len(self.recents)} appel(s) récent(s).")
        self._set_status("Journal d’appels actualisé.")

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
        for name, number, call_type, when in self.recents:
            row = Adw.ActionRow.new()
            row.set_title(GLib.markup_escape_text(name))
            row.set_subtitle(" · ".join(part for part in (number, call_type, when) if part))
            icon = "call-incoming-symbolic" if call_type == "Entrant" else "call-outgoing-symbolic" if call_type == "Sortant" else "call-missed-symbolic" if call_type == "Manqué" else "call-start-symbolic"
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            button = Gtk.Button.new_from_icon_name("call-start-symbolic")
            button.add_css_class("flat")
            button.set_tooltip_text("Rappeler")
            button.connect("clicked", lambda *_args, value=number: self._call_number(value))
            row.add_suffix(button)
            self.recent_list.append(row)

    def _call_number(self, number):
        self.number_entry.set_text(number)
        self.stack.set_visible_child_name("calls")
        self._place_call()

    def _open_sms(self, number):
        self.sms_number_entry.set_text(number)
        self.stack.set_visible_child_name("sms")
        self.sms_body_view.grab_focus()

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

    def _place_call(self, *_args):
        active = self._active_device()
        if not active:
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
        if self._queue_droidlink_call("place_call", number=number):
            return
        self._run_adb_command(
            ["shell", "am", "start", "-a", "android.intent.action.DIAL", "-d", f"tel:{number}"],
            "Ouverture du composeur Android…",
            lambda result, error: self._after_dial(result, error, active[0]),
        )

    def _after_dial(self, result, error, serial):
        if error or not result or result.returncode != 0:
            self._set_status(f"Impossible d’ouvrir le composeur : {error or (result.stderr if result else '')}", True)
            return
        if self._launch_scrcpy(serial):
            self._set_status("Composeur du téléphone ouvert : confirme l’appel dans Android; la SIM du cellulaire passera l’appel.")

    def _answer_call(self, *_args):
        if self._queue_droidlink_call("answer_call"):
            return
        active = self._active_device()
        if not active:
            return
        self._launch_scrcpy(active[0])
        self._run_adb_command(
            ["shell", "input", "keyevent", "KEYCODE_HEADSETHOOK"],
            "Commande Répondre envoyée au téléphone…",
            lambda result, error: self._command_status(result, error, "Réponse demandée."),
        )

    def _reject_call(self, *_args):
        if self._queue_droidlink_call("reject_call"):
            return
        self._run_adb_command(
            ["shell", "input", "keyevent", "KEYCODE_ENDCALL"],
            "Commande de refus d’appel envoyée au téléphone…",
            lambda result, error: self._command_status(result, error, "Refus demandé."),
        )

    def _hangup_call(self, *_args):
        if self._queue_droidlink_call("hangup_call"):
            return
        self._run_adb_command(
            ["shell", "input", "keyevent", "KEYCODE_ENDCALL"],
            "Commande de fin d’appel envoyée…",
            lambda result, error: self._command_status(result, error, "Fin d’appel demandée."),
        )

    def _queue_droidlink_call(self, action, number=""):
        if not self.droidlink_bridge:
            return False
        state = self.droidlink_bridge.snapshot()
        if not state["paired"]:
            return False
        phone = state.get("phone", {})
        if not phone.get("dialerRole"):
            self._set_status("Choisis DroidLink comme application Téléphone sur Android pour les appels intégrés.", True)
            return True
        if not phone.get("notificationAccess"):
            self._set_status("Active l’accès aux notifications dans DroidLink → Accès pour recevoir les commandes du PC.", True)
            return True
        if action == "place_call" and not phone.get("callPhoneAccess"):
            self._set_status("Autorise les appels sortants dans DroidLink → Accès, puis réessaie.", True)
            return True
        details = {"number": number} if number else {}
        if not self.droidlink_bridge.enqueue_command(action, "", **details):
            self._set_status("DroidLink n’est pas associé au téléphone.", True)
            return False
        label = {
            "place_call": f"Appel de {number} demandé à DroidLink…",
            "answer_call": "Réponse à l’appel demandée à DroidLink…",
            "reject_call": "Refus de l’appel demandé à DroidLink…",
            "hangup_call": "Fin de l’appel demandée à DroidLink…",
            "call_audio_bluetooth": "Route audio Bluetooth demandée à Android…",
            "call_audio_speaker": "Haut-parleur du téléphone demandé…",
            "call_audio_earpiece": "Écouteur du téléphone demandé…",
            "call_mute_on": "Microphone coupé…",
            "call_mute_off": "Microphone réactivé…",
        }.get(action, "Commande d’appel envoyée à DroidLink.")
        self._set_status(label)
        return True

    def _set_call_audio_route(self, route):
        action = {
            "bluetooth": "call_audio_bluetooth",
            "speaker": "call_audio_speaker",
            "earpiece": "call_audio_earpiece",
        }.get(route)
        if action:
            self._queue_droidlink_call(action)

    def _set_call_muted(self, button):
        if self._updating_call_audio_controls:
            return
        self._queue_droidlink_call("call_mute_on" if button.get_active() else "call_mute_off")

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

    def _launch_scrcpy(self, serial):
        if not self.scrcpy:
            self._set_status("scrcpy est introuvable. Installez scrcpy, puis actualisez l’application.", True)
            return
        current = self.scrcpy_processes.get(serial)
        if current and current.poll() is None:
            self._set_status("La fenêtre scrcpy de ce téléphone est déjà ouverte.")
            return True
        try:
            self.scrcpy_processes[serial] = subprocess.Popen(
                [self.scrcpy, "--serial", serial, "--no-audio"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._set_status("scrcpy ouvert; le son d’appel reste sur Bluetooth.")
            return True
        except OSError as error:
            self._set_status(f"Lancement de scrcpy impossible : {error}", True)
            return False

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
        while child := self.apps_flow.get_first_child():
            self.apps_flow.remove(child)
        packages = [package for package in self.app_packages if self.app_search_text in package.casefold() or self.app_search_text in self._display_app_label(package).casefold()]
        for package in packages:
            button = Gtk.Button()
            button.add_css_class("card")
            button.set_tooltip_text(package)
            button.set_size_request(128, 112)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
            content.set_margin_top(12)
            content.set_margin_bottom(12)
            content.set_margin_start(8)
            content.set_margin_end(8)
            icon = Gtk.Image()
            pixbuf = self.app_icons.get(package)
            if pixbuf:
                icon.set_from_pixbuf(
                    pixbuf.scale_simple(56, 56, GdkPixbuf.InterpType.BILINEAR) or pixbuf
                )
                icon.set_size_request(56, 56)
            else:
                icon.set_from_icon_name(self._app_icon(package))
                icon.set_pixel_size(40)
            content.append(icon)
            label = Gtk.Label(label=self._display_app_label(package))
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(15)
            content.append(label)
            button.set_child(content)
            button.connect("clicked", lambda _button, value=package: self._launch_app(value))
            context_click = Gtk.GestureClick.new()
            context_click.set_button(3)
            context_click.connect("pressed", self._show_app_context, package, button)
            button.add_controller(context_click)
            self.apps_flow.append(button)

    def _show_app_context(self, _gesture, _presses, _x, _y, package, anchor):
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
            label=f"{self._app_label(package)} ({package}) sera retirée du profil Android actif. Une application système pourra être restaurée par Android.",
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
                    f"Désinstallation de {self._app_label(package)}…",
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
        self._set_status(f"{self._app_label(package)} retirée du profil Android.")
        self._refresh_apps()

    def _open_app_info(self, package):
        self._run_adb_command(
            ["shell", "am", "start", "-a", "android.settings.APPLICATION_DETAILS_SETTINGS", "-d", f"package:{package}"],
            f"Ouverture des informations de {self._app_label(package)}…",
            lambda result, error: self._command_status(result, error, "Informations ouvertes sur le téléphone."),
        )

    def _launch_app(self, package):
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
            self._set_status("Nom de paquet invalide.", True)
            return
        self._run_adb_command(
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            f"Lancement de {package}…",
            lambda result, error: self._command_status(result, error, f"Application lancée : {package}"),
        )

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
        self.gallery_cards.clear()
        self.gallery_requested_thumbnails.clear()
        self.gallery_selected = None
        for photo in self.gallery_photos:
            photo_id = int(photo["id"])
            card = Gtk.Button()
            card.add_css_class("card")
            card.set_size_request(154, 150)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            content.set_margin_start(8)
            content.set_margin_end(8)
            thumbnail = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
            thumbnail.set_pixel_size(64)
            thumbnail.set_size_request(134, 100)
            content.append(thumbnail)
            name = Gtk.Label(label=photo.get("name") or "Image", xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(20)
            name.add_css_class("caption")
            content.append(name)
            card.set_child(content)
            card.connect("clicked", lambda _button, record=photo: self._select_gallery_photo(record))
            self.photos_flow.append(card)
            self.gallery_cards[photo_id] = {"button": card, "image": thumbnail, "photo": photo}
        count = len(self.gallery_photos)
        self.photos_status.set_text(f"{count:,} photo{'s' if count != 1 else ''} trouvée{'s' if count != 1 else ''} · les vignettes se chargent quand tu navigues.".replace(",", " "))
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
        if not self.droidlink_bridge:
            return
        state = self.droidlink_bridge.snapshot()
        if not state["paired"]:
            return
        stop = min(len(self.gallery_photos), start + count)
        for photo in self.gallery_photos[start:stop]:
            photo_id = int(photo["id"])
            if photo_id in self.gallery_requested_thumbnails:
                continue
            if self.droidlink_bridge.enqueue_command("photo_thumbnail", "", photoId=photo_id):
                self.gallery_requested_thumbnails.add(photo_id)

    def _display_photo_thumbnail(self, event):
        try:
            photo_id = int(event.get("id", 0))
            encoded = event.get("jpeg", "")
            data = base64.b64decode(encoded, validate=True)
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            card = self.gallery_cards.get(photo_id)
            if card and pixbuf:
                card["image"].set_from_pixbuf(pixbuf)
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

    def _refresh_info(self, *_args):
        active = self._active_device()
        if not active or self.busy:
            return
        serial = active[0]
        self._set_status("Lecture des informations du téléphone…")
        self._set_busy(True)

        def worker():
            commands = {
                "Modèle": ["shell", "getprop", "ro.product.model"],
                "Version Android": ["shell", "getprop", "ro.build.version.release"],
                "Fabricant": ["shell", "getprop", "ro.product.manufacturer"],
                "Batterie": ["shell", "dumpsys", "battery"],
                "Stockage partagé": ["shell", "df", "-h", "/sdcard"],
            }
            values = {}
            errors = []
            for label, args in commands.items():
                try:
                    result = subprocess.run(
                        [self.adb, "-s", serial, *args],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    if result.returncode == 0:
                        values[label] = result.stdout.strip()
                    else:
                        errors.append(label)
                except (OSError, subprocess.TimeoutExpired):
                    errors.append(label)
            GLib.idle_add(self._info_result, values, errors)

        threading.Thread(target=worker, daemon=True).start()

    def _info_result(self, values, errors):
        self._set_busy(False)
        lines = []
        for label, value in values.items():
            if label == "Batterie":
                match = re.search(r"(?m)^\s*level:\s*(\d+)", value)
                value = f"{match.group(1)} %" if match else "Disponible"
            elif label == "Stockage partagé":
                value = next((line for line in value.splitlines() if "/sdcard" in line), value)
            lines.append(f"{label} : {value or '—'}")
        self.info_view.set_text("\n".join(lines) if lines else "Aucune information disponible.")
        if errors:
            self._set_status("Informations partielles; certaines commandes ADB ont échoué.", True)
        else:
            self._set_status("Informations actualisées.")
        return GLib.SOURCE_REMOVE

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

    def _open_android_notification(self, _action, target):
        key = target.get_string() if target else ""
        if not hasattr(self, "window"):
            return
        event = next(
            (item for item in self.window.droidlink_messages if item.get("key") == key),
            {},
        )
        page = "sms" if event.get("isSmsApp") else "droidlink"
        self.window.stack.set_visible_child_name(page)
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
        if not hasattr(self, "window"):
            self.window = DroidDeskWindow(self)
        self.window.present()


def main():
    Adw.init()
    return DroidDeskApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
