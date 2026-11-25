#!/usr/bin/env python3
"""GTK 3 Ayatana AppIndicator for the GTK 4 DroidDesk application."""

import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, Gtk


APP_ID = "io.github.example.DroidDesk"


def activate_action(action):
    subprocess.Popen(
        ["gapplication", "action", APP_ID, action],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    indicator = AyatanaAppIndicator3.Indicator.new(
        "droiddesk",
        "droiddesk",
        AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    show_item = Gtk.MenuItem(label="Afficher DroidDesk")
    show_item.connect("activate", lambda *_args: activate_action("show"))
    menu.append(show_item)
    menu.append(Gtk.SeparatorMenuItem())
    quit_item = Gtk.MenuItem(label="Quitter DroidDesk")
    quit_item.connect("activate", lambda *_args: activate_action("quit"))
    menu.append(quit_item)
    menu.show_all()
    indicator.set_menu(menu)
    Gtk.main()


if __name__ == "__main__":
    main()
