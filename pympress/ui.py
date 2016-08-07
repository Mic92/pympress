#       ui.py
#
#       Copyright 2010 Thomas Jost <thomas.jost@gmail.com>
#       Copyright 2015 Cimbali <me@cimba.li>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.

"""
:mod:`pympress.ui` -- GUI management
------------------------------------

This module contains the whole graphical user interface of pympress, which is
made of two separate windows: the Content window, which displays only the
current page in full size, and the Presenter window, which displays both the
current and the next page, as well as a time counter and a clock.

Both windows are managed by the :class:`~pympress.ui.UI` class.
"""

from __future__ import print_function

import os, os.path, subprocess
import sys
import time

import pkg_resources

import gi
import cairo
gi.require_version('Gtk', '3.0')
from gi.repository import GObject, Gtk, Gdk, Pango, GLib

#: "Regular" PDF file (without notes)
PDF_REGULAR      = 0
#: Content page (left side) of a PDF file with notes
PDF_CONTENT_PAGE = 1
#: Notes page (right side) of a PDF file with notes
PDF_NOTES_PAGE   = 2

import pympress.document
import pympress.surfacecache
import pympress.util
import pympress.slideselector
try:
    import pympress.vlcvideo
    vlc_enabled = True
except:
    vlc_enabled = False
    print(_("Warning: video support is disabled"))

from pympress.util import IS_POSIX, IS_MAC_OS, IS_WINDOWS

if IS_WINDOWS:
    try:
        import winreg
    except ImportError:
        import _winreg as winreg
else:
    try:
        gi.require_version('GdkX11', '3.0')
        from gi.repository import GdkX11
    except:
        pass

try:
    PermissionError()
except NameError:
    class PermissionError(Exception):
        pass

media_overlays = {}

class UI:
    """ Pympress GUI management.
    """

    #: :class:`~pympress.surfacecache.SurfaceCache` instance.
    cache = None

    #: Content window, as a :class:`Gtk.Window` instance.
    c_win = Gtk.Window(Gtk.WindowType.TOPLEVEL)
    #: :class:`~Gtk.AspectFrame` for the Content window.
    c_frame = Gtk.AspectFrame(yalign=0, ratio=4./3., obey_child=False)
    #: :class:`~Gtk.Overlay` for the Content window.
    c_overlay = Gtk.Overlay()
    #: :class:`~Gtk.DrawingArea` for the Content window.
    c_da = Gtk.DrawingArea()

    #: Presenter window, as a :class:`Gtk.Window` instance.
    p_win = Gtk.Window(Gtk.WindowType.TOPLEVEL)
    #: :class:`~Gtk.Box` for the Presenter window.
    p_central = Gtk.Box()
    #: :class:`~Gtk.Paned` containg current/notes slide on one side, current/next slide/annotations
    hpaned = Gtk.Paned()
    #: :class:`~Gtk.AspectFrame` for the current slide in the Presenter window.
    p_frame_cur = Gtk.AspectFrame(xalign=0, yalign=0, ratio=4./3., obey_child=False)
    #: :class:`~Gtk.DrawingArea` for the current slide in the Presenter window.
    p_da_cur = Gtk.DrawingArea()
    #: Slide counter :class:`~Gtk.Label` for the current slide.
    label_cur = Gtk.Label()
    #: Slide counter :class:`~Gtk.Label` for the last slide.
    label_last = Gtk.Label()
    #: :class:`~Gtk.EventBox` associated with the slide counter label in the Presenter window.
    eb_cur = Gtk.EventBox()
    #: forward keystrokes to the Content window even if the window manager puts Presenter on top
    editing_cur = False
    #: :class:`~Gtk.SpinButton` used to switch to another slide by typing its number.
    spin_cur = None
    #: forward keystrokes to the Content window even if the window manager puts Presenter on top
    editing_cur_ett = False
    #: Estimated talk time :class:`~gtk.Label` for the talk.
    label_ett = Gtk.Label()
    #: :class:`~gtk.EventBox` associated with the estimated talk time.
    eb_ett = Gtk.EventBox()
    #: :class:`~gtk.Entry` used to set the estimated talk time.
    entry_ett = Gtk.Entry()

    #: :class:`~Gtk.AspectFrame` for the next slide in the Presenter window.
    p_frame_next = Gtk.AspectFrame(yalign=0, ratio=4./3., obey_child=False)
    #: :class:`~Gtk.DrawingArea` for the next slide in the Presenter window.
    p_da_next = Gtk.DrawingArea()
    #: :class:`~Gtk.AspectFrame` for the current slide copy in the Presenter window.
    p_frame_pres = Gtk.AspectFrame(yalign=0, ratio=4./3., obey_child=False)
    #: :class:`~Gtk.DrawingArea` for the current slide copy in the Presenter window.
    p_da_pres = Gtk.DrawingArea()

    #: :class:`~Gtk.Frame` for the annotations in the Presenter window.
    p_frame_annot = Gtk.Frame()

    #: Elapsed time :class:`~Gtk.Label`.
    label_time = Gtk.Label()
    #: Clock :class:`~Gtk.Label`.
    label_clock = Gtk.Label()

    #: Time at which the counter was started.
    start_time = 0
    #: Time elapsed since the beginning of the presentation.
    delta = 0
    #: Estimated talk time.
    est_time = 0
    #: Timer paused status.
    paused = True

    #: Fullscreen toggle. By config value, start in fullscreen mode.
    c_win_fullscreen = False

    #: Current :class:`~pympress.document.Document` instance.
    doc = None

    #: Whether to use notes mode or not
    notes_mode = False

    #: Whether to display annotations or not
    show_annotations = True

    #: Whether to display big buttons or not
    show_bigbuttons = True
    #: :class:`Gtk.ToolButton` big button for touch screens, go to previous slide
    prev_button = None
    #: :class:`Gtk.ToolButton` big button for touch screens, go to next slide
    next_button = None
    #: :class:`Gtk.ToolButton` big button for touch screens, go to scribble on screen
    highlight_button = None

    #: number of page currently displayed in Controller window's miniatures
    page_preview_nb = 0

    #: remember DPMS setting before we change it
    dpms_was_enabled = None

    #: track state of preview window
    p_win_maximized = True

    #: :class:`configparser.RawConfigParser` to remember preferences
    config = None

    #: track whether we blank the screen
    blanked = False

    #: :class:`Gdk.RGBA` The default color of the info labels
    label_color_default = None
    #: :class:`Gdk.RGBA` The color of the elapsed time label if the estimated talk time is reached
    label_color_ett_reached = None
    #: :class:`Gdk.RGBA` The color of the elapsed time label if the estimated talk time is exceeded by 2:30 minutes
    label_color_ett_info = None
    #: :class:`Gdk.RGBA` The color of the elapsed time label if the estimated talk time is exceeded by 5 minutes
    label_color_ett_warn = None

    #: The containing widget for the annotations
    scrollable_treelist = None
    #: Making the annotations list scroll if it's too long
    scrolled_window = Gtk.ScrolledWindow()

    #: Whether we are displaying the interface to scribble on screen and the overlays containing said scribbles
    scribbling_mode = None
    #: list of scribbles to be drawn, as pairs of  :class:`Gdk.RGBA`
    scribble_list = []
    #: :class:`Gdk.RGBA` current color of the scribbling tool
    scribble_color = None
    #: `int` current stroke width of the scribbling tool
    scribble_width = 1
    #: :class:`~Gtk.Overlay` that is added or removed when scribbling is toggled, contains buttons and scribble drawing area
    scribble_overlay = None
    #: :class:`~Gtk.DrawingArea` for the scribbling in the Presenter window. Actually redraws the slide.
    scribble_c_da = None
    #: :class:`~Gtk.DrawingArea` for the scribbles in the Content window. On top of existing overlays and slide.
    scribble_p_da = None
    #: :class:`~Gtk.EventBox` for the scribbling in the Presenter window, captures freehand drawing
    scribble_c_eb = None
    #: :class:`~Gtk.EventBox` for the scribbling in the Content window, captures freehand drawing
    scribble_p_eb = None
    #: :class:`~Gtk.AspectFrame` for the slide in the Presenter's highlight mode
    scribble_p_frame = None

    def __init__(self, docpath = None, ett = 0):
        """
        :param docpath: the path to the document to open
        :type  docpath: string
        :param ett: the estimated (intended) talk time
        :type  ett: int
        """
        self.est_time = ett
        self.config = pympress.util.load_config()
        self.blanked = self.config.getboolean('content', 'start_blanked')

        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            pympress.util.get_style_provider(),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Document
        self.doc = pympress.document.Document.create(self.on_page_change, docpath or self.pick_file())

        # Use notes mode by default if the document has notes
        self.notes_mode = self.doc.has_notes()
        self.show_annotations = not self.notes_mode

        # Surface cache
        self.cache = pympress.surfacecache.SurfaceCache(self.doc, self.config.getint('cache', 'maxpages'))

        # Make and populate windows
        self.make_cwin()
        self.make_pwin()
        self.setup_screens()

        # Common to both windows
        icon_list = pympress.util.load_icons()
        self.c_win.set_icon_list(icon_list)
        self.p_win.set_icon_list(icon_list)

        # Setup timer
        GObject.timeout_add(250, self.update_time)

        # Connect events
        self.add_events()

        # Show all windows
        self.c_win.show_all()
        self.p_win.show_all()

        # Add media
        self.replace_media_overlays()
        self.setup_scribbling()

        # Queue some redraws
        self.c_overlay.queue_draw()
        self.c_da.queue_draw()
        self.p_da_cur.queue_draw()
        self.p_da_next.queue_draw()
        if self.notes_mode:
            self.p_da_pres.queue_draw()

        # Adjust default visibility of items
        self.p_frame_annot.set_visible(self.show_annotations)
        self.p_frame_pres.set_visible(self.notes_mode)
        self.prev_button.set_visible(self.show_bigbuttons)
        self.next_button.set_visible(self.show_bigbuttons)
        self.highlight_button.set_visible(self.show_bigbuttons)

        self.label_color_default = self.label_time.get_style_context().get_color(Gtk.StateType.NORMAL)


    def make_cwin(self):
        """ Creates and initializes the content window.
        """
        black = Gdk.Color(0, 0, 0)

        # Content window
        self.c_win.set_name('ContentWindow')
        self.c_win.set_title(_("pympress content"))
        self.c_win.set_default_size(1067, 600)
        self.c_win.modify_bg(Gtk.StateType.NORMAL, black)
        self.c_win.add(self.c_frame)

        self.c_frame.modify_bg(Gtk.StateType.NORMAL, black)
        self.c_frame.set_shadow_type(Gtk.ShadowType.NONE)
        self.c_frame.set_property("yalign", self.config.getfloat('content', 'yalign'))
        self.c_frame.set_property("xalign", self.config.getfloat('content', 'xalign'))
        self.c_frame.add(self.c_overlay)

        self.c_overlay.props.margin = 0
        self.c_frame.props.border_width = 0
        self.c_overlay.add(self.c_da)

        self.c_da.props.expand = True
        self.c_da.props.halign = Gtk.Align.FILL
        self.c_da.props.valign = Gtk.Align.FILL
        self.c_da.set_name("c_da")
        if self.notes_mode:
            self.cache.add_widget("c_da", pympress.document.PDF_CONTENT_PAGE)
        else:
            self.cache.add_widget("c_da", pympress.document.PDF_REGULAR)

        pr = self.doc.current_page().get_aspect_ratio(self.notes_mode)
        self.c_frame.set_property("ratio", pr)

    def goto_prev(self, *args):
        self.doc.goto_prev()

    def goto_next(self, *args):
        self.doc.goto_next()

    def make_pwin(self):
        """ Creates and initializes the presenter window.
        """
        builder = Gtk.Builder()
        builder.add_from_file(os.path.join("share", "presenter.glade"))
        builder.connect_signals(self)
        # Presenter window
        self.p_win = builder.get_object("p_win")

        bigvbox = builder.get_object("bigvbox")
        menubar = self.make_menubar()
        bigvbox.pack_start(menubar, False, False, 0)
        bigvbox.reorder_child(menubar, 0)

        self.hpaned = builder.get_object("hpaned")
        self.p_frame_cur = builder.get_object("p_frame_cur")
        self.p_frame_pres = builder.get_object("p_frame_pres")
        self.p_da_cur = builder.get_object("p_da_cur")
        self.p_da_pres = builder.get_object("p_da_pres")
        self.p_da_next = builder.get_object("p_da_next")
        self.p_da_cur.set_name("p_da_cur")
        self.p_da_pres.set_name("p_da_pres")
        self.p_da_next.set_name("p_da_next")
        self.p_frame_next = builder.get_object("p_frame_next")
        self.p_frame_annot = builder.get_object("p_frame_annot")
        self.show_bigbuttons = self.config.getboolean('presenter', 'show_bigbuttons')
        self.prev_button = builder.get_object("prev_button")
        self.next_button = builder.get_object("next_button")
        self.highlight_button = builder.get_object("highlight_button")
        self.label_cur = builder.get_object("label_cur")
        self.label_last = builder.get_object("label_last")
        self.hb_cur = builder.get_object("hb_cur")
        self.eb_cur = builder.get_object("eb_cur")
        self.label_time = builder.get_object("label_time")
        self.label_ett = builder.get_object("label_ett")
        self.eb_ett = builder.get_object("eb_ett")
        self.label_clock = builder.get_object("label_clock")
        self.p_central = builder.get_object("p_central")
        
        self.spin_cur = pympress.slideselector.SlideSelector(self, self.doc.pages_number())
        self.spin_cur.set_alignment(0.5)

        if self.notes_mode:
            self.cache.add_widget("p_da_cur", PDF_NOTES_PAGE)
            self.cache.add_widget("p_da_pres", PDF_CONTENT_PAGE, True)
            self.cache.add_widget("p_da_next", PDF_CONTENT_PAGE)
        else:
            self.cache.add_widget("p_da_cur", PDF_REGULAR)
            self.cache.add_widget("p_da_next", PDF_REGULAR)
            self.cache.add_widget("p_da_pres", PDF_REGULAR, False)


        # Annotations
        self.scrolled_window = builder.get_object("scrolled_window")
        self.scrollable_treelist = builder.get_object("scrollable_treelist")
        self.annotation_renderer = Gtk.CellRendererText()
        self.annotation_renderer.props.wrap_mode = Pango.WrapMode.WORD_CHAR

        column = Gtk.TreeViewColumn(None, self.annotation_renderer, text=0)
        column.props.sizing = Gtk.TreeViewColumnSizing.AUTOSIZE
        column.set_fixed_width(1)

        self.scrollable_treelist.set_model(Gtk.ListStore(str))
        self.scrollable_treelist.append_column(column)

        self.scrolled_window.set_hexpand(True)

        # Load color from CSS
        style_context = self.label_time.get_style_context()
        style_context.add_class("ett-reached")
        self.label_time.show();
        self.label_color_ett_reached = style_context.get_color(Gtk.StateType.NORMAL)
        style_context.remove_class("ett-reached")
        style_context.add_class("ett-info")
        self.label_time.show();
        self.label_color_ett_info = style_context.get_color(Gtk.StateType.NORMAL)
        style_context.remove_class("ett-info")
        style_context.add_class("ett-warn")
        self.label_time.show();
        self.label_color_ett_warn = style_context.get_color(Gtk.StateType.NORMAL)
        style_context.remove_class("ett-warn")
        style_context.add_class("info-label")
        self.label_time.show();

        # set default values
        self.label_last.set_text("/{}".format(self.doc.pages_number()))
        self.label_ett.set_text("{:02}:{:02}".format(*divmod(self.est_time, 60)))

        self.p_win.show_all()

        if gi.version_info >= (3,16): self.hpaned.set_wide_handle(True)
        pane_size = self.config.getfloat('presenter', 'slide_ratio')
        avail_size = self.p_frame_cur.get_allocated_width() + self.p_frame_next.get_allocated_width()
        self.hpaned.set_position(int(round(pane_size * avail_size)))
        self.on_page_change(False)
        GLib.idle_add(self.resize_annotation_list)


    def add_events(self):
        """ Connects the events we want to the different widgets.
        """
        self.c_win.connect("destroy", self.save_and_quit)
        self.c_win.connect("delete-event", self.save_and_quit)

        self.c_da.connect("draw", self.on_draw)
        self.c_da.connect("configure-event", self.on_configure_da)

        self.p_win.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.SCROLL_MASK)
        self.c_win.connect("window-state-event", self.on_window_state_event)
        self.c_win.connect("configure-event", self.on_configure_win)

        self.c_win.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.SCROLL_MASK)
        self.c_win.connect("key-press-event", self.on_navigation)
        self.c_win.connect("scroll-event", self.on_navigation)

        # Hyperlinks
        self.c_da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.c_da.connect("button-press-event", self.on_link)
        self.c_da.connect("motion-notify-event", self.on_link)

        self.p_da_cur.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK)



    def setup_screens(self):
        """ Sets up the position of the windows
        """
        # If multiple monitors, apply windows to monitors according to config
        screen = self.p_win.get_screen()
        if screen.get_n_monitors() > 1:
            c_monitor = self.config.getint('content', 'monitor')
            p_monitor = self.config.getint('presenter', 'monitor')
            p_full = self.config.getboolean('presenter', 'start_fullscreen')
            c_full = self.config.getboolean('content', 'start_fullscreen')

            if c_monitor == p_monitor and (c_full or p_full):
                print(_("Warning: Content and presenter window must not be on the same monitor if you start full screen!"), file=sys.stderr)
                p_monitor = 0 if c_monitor > 0 else 1

            p_bounds = screen.get_monitor_geometry(p_monitor)
            self.p_win.move(p_bounds.x, p_bounds.y)
            if p_full:
                self.p_win.fullscreen()
            else:
                self.p_win.maximize()

            c_bounds = screen.get_monitor_geometry(c_monitor)
            self.c_win.move(c_bounds.x, c_bounds.y)
            if c_full:
                self.c_win.fullscreen()


    def make_menubar(self):
        """ Creates and initializes the menu bar.

        :return: the menu bar
        :rtype: :class:`Gtk.Widget`
        """
        # UI Manager for menu
        ui_manager = Gtk.UIManager()

        # UI description
        ui_desc = '''
        <menubar name="MenuBar">
          <menu action="File">
            <menuitem action="Quit"/>
          </menu>
          <menu action="Presentation">
            <menuitem action="Pause timer"/>
            <menuitem action="Reset timer"/>
            <menuitem action="Set talk time"/>
            <menuitem action="Fullscreen"/>
            <menuitem action="Swap screens"/>
            <menuitem action="Notes mode"/>
            <menuitem action="Blank screen"/>
            <menuitem action="Align content"/>
            <menuitem action="Annotations"/>
            <menuitem action="Big buttons"/>
            <menuitem action="Highlight"/>
          </menu>
          <menu action="Navigation">
            <menuitem action="Next"/>
            <menuitem action="Previous"/>
            <menuitem action="First"/>
            <menuitem action="Last"/>
            <menuitem action="Go to..."/>
          </menu>
          <menu action="Starting Configuration">
            <menuitem action="Content blanked"/>
            <menuitem action="Content fullscreen"/>
            <menuitem action="Presenter fullscreen"/>
          </menu>
          <menu action="Help">
            <menuitem action="About"/>
          </menu>
        </menubar>'''
        ui_manager.add_ui_from_string(ui_desc)

        # Accelerator group
        accel_group = ui_manager.get_accel_group()
        self.p_win.add_accel_group(accel_group)

        # Action group
        action_group = Gtk.ActionGroup('MenuBar')
        # Name, stock id, label, accelerator, tooltip, action [, is_active]
        action_group.add_actions([
            ('File',         None,           _('_File')),
            ('Presentation', None,           _('_Presentation')),
            ('Navigation',   None,           _('_Navigation')),
            ('Starting Configuration', None, _('_Starting Configuration')),
            ('Help',         None,           _('_Help')),

            ('Quit',         Gtk.STOCK_QUIT, _('_Quit'),        'q',     None, self.save_and_quit),
            ('Reset timer',  None,           _('_Reset timer'), 'r',     None, self.reset_timer),
            ('Set talk time',None,           _('Set talk _Time'),'t',    None, self.on_label_ett_event),
            ('About',        None,           _('_About'),       None,    None, self.menu_about),
            ('Swap screens', None,           _('_Swap screens'),'s',     None, self.swap_screens),
            ('Align content',None,           _('_Align content'),None,   None, self.adjust_frame_position),

            ('Next',         None,           _('_Next'),        'Right', None, self.doc.goto_next),
            ('Previous',     None,           _('_Previous'),    'Left',  None, self.doc.goto_prev),
            ('First',        None,           _('_First'),       'Home',  None, self.doc.goto_home),
            ('Last',         None,           _('_Last'),        'End',   None, self.doc.goto_end),
            ('Go to...',     None,           _('_Go to...'),    'g',     None, self.on_label_event),
        ])
        action_group.add_toggle_actions([
            ('Pause timer',  None,           _('_Pause timer'), 'p',     None, self.switch_pause,         True),
            ('Fullscreen',   None,           _('_Fullscreen'),  'f',     None, self.switch_fullscreen,    self.config.getboolean('content', 'start_fullscreen')),
            ('Notes mode',   None,           _('_Note mode'),   'n',     None, self.switch_mode,          self.notes_mode),
            ('Blank screen', None,           _('_Blank screen'),'b',     None, self.switch_blanked,       self.blanked),
            ('Content blanked',      None,   _('Content blanked'),       None, None, self.switch_start_blanked,    self.config.getboolean('content', 'start_blanked')),
            ('Content fullscreen',   None,   _('Content fullscreen'),    None, None, self.switch_start_fullscreen, self.config.getboolean('content', 'start_fullscreen')),
            ('Presenter fullscreen', None,   _('Presenter fullscreen'),  None, None, self.switch_start_fullscreen, self.config.getboolean('presenter', 'start_fullscreen')),
            ('Annotations',  None,           _('_Annotations'), 'a',     None, self.switch_annotations,   self.show_annotations),
            ('Big buttons',  None,           _('Big buttons'),   None,   None, self.switch_bigbuttons,    self.config.getboolean('presenter', 'show_bigbuttons')),
            ('Highlight',    None,           _('_Highlight'),   'h',     None, self.switch_scribbling,    False),
        ])
        ui_manager.insert_action_group(action_group)

        # Add menu bar to the window
        h = ui_manager.get_widget('/MenuBar/Help')
        h.set_right_justified(True)
        return ui_manager.get_widget('/MenuBar')


    def add_annotations(self, annotations):
        """ Insert text annotations into the tree view that displays them.
        """
        list_annot = Gtk.ListStore(str)

        for annot in annotations:
            list_annot.append(('\xe2\x97\x8f '+annot,))

        self.scrollable_treelist.set_model(list_annot)
        self.resize_annotation_list


    def run(self):
        """ Run the GTK main loop.
        """
        Gtk.main()


    def save_and_quit(self, *args):
        """ Save configuration and exit the main loop.
        """
        cur_pane_size = self.p_frame_cur.get_allocated_width()
        next_pane_size = self.p_frame_next.get_allocated_width()
        ratio = float(cur_pane_size) / (cur_pane_size + next_pane_size)
        self.config.set('presenter', 'slide_ratio', "{0:.2f}".format(ratio))

        self.doc.cleanup_media_files()

        pympress.util.save_config(self.config)
        Gtk.main_quit()


    def pick_file(self):
        """ Ask the user which file he means to open.
        """
        # Use a GTK file dialog to choose file
        dialog = Gtk.FileChooserDialog(_('Open...'), self.p_win,
                                       Gtk.FileChooserAction.OPEN,
                                       (Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_position(Gtk.WindowPosition.CENTER)

        filter = Gtk.FileFilter()
        filter.set_name(_('PDF files'))
        filter.add_mime_type('application/pdf')
        filter.add_pattern('*.pdf')
        dialog.add_filter(filter)

        filter = Gtk.FileFilter()
        filter.set_name(_('All files'))
        filter.add_pattern('*')
        dialog.add_filter(filter)

        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            name = dialog.get_filename()
            dialog.destroy()
        else:
            dialog.destroy()

            # Use a GTK dialog to tell we need a file
            msg=_("""No file selected!\n\nYou can specify the PDF file to open on the command line if you don't want to use the "Open File" dialog.""")
            dialog = Gtk.MessageDialog(type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, message_format=msg, parent=self.p_win)
            dialog.set_position(Gtk.WindowPosition.CENTER)
            dialog.run()
            sys.exit(1)

        return os.path.abspath(name)


    def menu_about(self, widget=None, event=None):
        """ Display the "About pympress" dialog.
        """
        about = Gtk.AboutDialog()
        about.set_program_name('pympress')
        about.set_version(pympress.__version__)
        about.set_copyright(_('Contributors:') + '\n' + pympress.__copyright__)
        about.set_comments(_('pympress is a little PDF reader written in Python using Poppler for PDF rendering and GTK for the GUI.\n')
                         + _('Some preferences are saved in ') + pympress.util.path_to_config())
        about.set_website('http://www.pympress.xyz/')
        try:
            about.set_logo(pympress.util.get_icon_pixbuf('pympress-128.png'))
        except Exception:
            print(_('Error loading icon for about window'))
        about.run()
        about.destroy()


    def page_preview(self, page_nb):
        """ Switch to another page and display it.

        This is a kind of event which is supposed to be called only from the
        :class:`~pympress.document.Document` class.

        :param unpause: ``True`` if the page change should unpause the timer,
           ``False`` otherwise
        :type  unpause: boolean
        """
        page_cur = self.doc.page(page_nb)
        page_next = self.doc.page(page_nb+1)

        self.page_preview_nb = page_nb

        # Aspect ratios and queue redraws
        pr = page_cur.get_aspect_ratio(self.notes_mode)
        self.p_frame_cur.set_property('ratio', pr)

        if self.notes_mode:
            self.p_frame_pres.set_property('ratio', pr)

        if page_next is not None:
            pr = page_next.get_aspect_ratio(self.notes_mode)
            self.p_frame_next.set_property('ratio', pr)

        # queue redraws
        self.p_da_cur.queue_draw()
        self.p_da_next.queue_draw()
        self.p_da_pres.queue_draw()

        self.add_annotations(page_cur.get_annotations())


        # Prerender the 4 next pages and the 2 previous ones
        cur = page_cur.number()
        page_max = min(self.doc.pages_number(), cur + 5)
        page_min = max(0, cur - 2)
        for p in list(range(cur+1, page_max)) + list(range(cur, page_min, -1)):
            self.cache.prerender(p)


    def on_page_change(self, unpause=True):
        """ Switch to another page and display it.

        This is a kind of event which is supposed to be called only from the
        :class:`~pympress.document.Document` class.

        :param unpause: ``True`` if the page change should unpause the timer,
           ``False`` otherwise
        :type  unpause: boolean
        """
        page_cur = self.doc.current_page()
        page_next = self.doc.next_page()

        self.add_annotations(page_cur.get_annotations())

        # Page change: resynchronize miniatures
        self.page_preview_nb = page_cur.number()

        # Aspect ratios
        pr = page_cur.get_aspect_ratio(self.notes_mode)
        self.c_frame.set_property('ratio', pr)
        self.p_frame_cur.set_property('ratio', pr)

        if self.notes_mode:
            self.p_frame_pres.set_property('ratio', pr)

        if page_next is not None:
            pr = page_next.get_aspect_ratio(self.notes_mode)
            self.p_frame_next.set_property('ratio', pr)

        # Remove scribbling if ongoing
        if self.scribbling_mode:
            self.switch_scribbling()
        del self.scribble_list[:]

        # Queue redraws
        self.c_da.queue_draw()
        self.p_da_cur.queue_draw()
        self.p_da_next.queue_draw()
        self.p_da_pres.queue_draw()

        # Start counter if needed
        if unpause:
            self.paused = False
            if self.start_time == 0:
                self.start_time = time.time()

        # Update display
        self.update_page_numbers()

        # Prerender the 4 next pages and the 2 previous ones
        page_max = min(self.doc.pages_number(), self.page_preview_nb + 5)
        page_min = max(0, self.page_preview_nb - 2)
        for p in list(range(self.page_preview_nb+1, page_max)) + list(range(self.page_preview_nb, page_min, -1)):
            self.cache.prerender(p)

        self.replace_media_overlays()


    def replace_media_overlays(self):
        """ Remove current media overlays, add new ones if page contains media.
        """
        if not vlc_enabled:
            return

        self.c_overlay.foreach(lambda child, *ignored: child.hide() if child is not self.c_da else None, None)

        page_cur = self.doc.current_page()
        pw, ph = page_cur.get_size()

        global media_overlays

        for relative_margins, filename, show_controls in page_cur.get_media():
            media_id = hash((relative_margins, filename, show_controls))

            if media_id not in media_overlays:
                v_da = pympress.vlcvideo.VLCVideo(self.c_overlay, show_controls, relative_margins)
                v_da.set_file(filename)

                media_overlays[media_id] = v_da


    @staticmethod
    def play_media(media_id):
        """ Static way of starting (playing) a media. Used by callbacks.
        """
        global media_overlays
        if media_id in media_overlays:
            media_overlays[media_id].play()


    def on_draw(self, widget, cairo_context):
        """ Manage draw events for both windows.

        This callback may be called either directly on a page change or as an
        event handler by GTK. In both cases, it determines which widget needs to
        be updated, and updates it, using the
        :class:`~pympress.surfacecache.SurfaceCache` if possible.

        :param widget: the widget to update
        :type  widget: :class:`Gtk.Widget`
        :param cairo_context: the Cairo context (or ``None`` if called directly)
        :type  cairo_context: :class:`cairo.Context`
        """

        if widget is self.c_da:
            # Current page
            if self.blanked:
                return
            page = self.doc.page(self.doc.current_page().number())
        elif widget is self.p_da_cur or widget is self.p_da_pres:
            # Current page 'preview'
            page = self.doc.page(self.page_preview_nb)
        else:
            page = self.doc.page(self.page_preview_nb+1)
            # No next page: just return so we won't draw anything
            if page is None:
                return

        # Instead of rendering the document to a Cairo surface (which is slow),
        # use a surface from the cache if possible.
        name = widget.get_name()
        nb = page.number()
        pb = self.cache.get(name, nb)
        wtype = self.cache.get_widget_type(name)

        if pb is None:
            # Cache miss: render the page, and save it to the cache
            ww, wh = widget.get_allocated_width(), widget.get_allocated_height()
            pb = widget.get_window().create_similar_surface(cairo.CONTENT_COLOR, ww, wh)

            cairo_prerender = cairo.Context(pb)
            page.render_cairo(cairo_prerender, ww, wh, wtype)

            cairo_context.set_source_surface(pb, 0, 0)
            cairo_context.paint()

            self.cache.set(name, nb, pb)
        else:
            # Cache hit: draw the surface from the cache to the widget
            cairo_context.set_source_surface(pb, 0, 0)
            cairo_context.paint()


    def on_configure_da(self, widget, event):
        """ Manage "configure" events for all drawing areas.

        In the GTK world, this event is triggered when a widget's configuration
        is modified, for example when its size changes. So, when this event is
        triggered, we tell the local :class:`~pympress.surfacecache.SurfaceCache`
        instance about it, so that it can invalidate its internal cache for the
        specified widget and pre-render next pages at a correct size.

        :param widget: the widget which has been resized
        :type  widget: :class:`Gtk.Widget`
        :param event: the GTK event, which contains the new dimensions of the
           widget
        :type  event: :class:`Gdk.Event`
        """
        self.cache.resize_widget(widget.get_name(), event.width, event.height)

        if widget is self.c_da and vlc_enabled:
            self.c_overlay.foreach(lambda child, *ignored: child.resize() if type(child) is pympress.vlcvideo.VLCVideo else None, None)
        elif widget is self.p_da_next:
            self.resize_annotation_list()


    def on_configure_win(self, widget, event):
        """ Manage "configure" events for both window widgets.

        :param widget: the window which has been moved or resized
        :type  widget: :class:`Gtk.Widget`
        :param event: the GTK event, which contains the new dimensions of the widget
        :type  event: :class:`Gdk.Event`
        """

        if widget is self.p_win:
            p_monitor = self.p_win.get_screen().get_monitor_at_window(self.p_frame_cur.get_parent_window())
            self.config.set('presenter', 'monitor', str(p_monitor))
        elif widget is self.c_win:
            c_monitor = self.c_win.get_screen().get_monitor_at_window(self.c_frame.get_parent_window())
            self.config.set('content', 'monitor', str(c_monitor))


    def on_navigation(self, widget, event):
        """ Manage events as mouse scroll or clicks for both windows.

        :param widget: the widget in which the event occured (ignored)
        :type  widget: :class:`Gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`Gdk.Event`
        """
        if event.type == Gdk.EventType.KEY_PRESS:
            name = Gdk.keyval_name(event.keyval)
            ctrl_pressed = event.get_state() & Gdk.ModifierType.CONTROL_MASK

            # send all to spinner if it is active to avoid key problems
            if self.editing_cur and self.spin_cur.on_keypress(widget, event):
                return True
            # send all to entry field if it is active to avoid key problems
            if self.editing_cur_ett and self.on_label_ett_event(widget, event):
                return True

            if self.paused and name == 'space':
                self.switch_pause()
            elif name in ['Right', 'Down', 'Page_Down', 'space']:
                self.doc.goto_next()
            elif name in ['Left', 'Up', 'Page_Up', 'BackSpace']:
                self.doc.goto_prev()
            elif name == 'Home':
                self.doc.goto_home()
            elif name == 'End':
                self.doc.goto_end()
            # sic - accelerator recognizes f not F
            elif name.upper() == 'F11' or name == 'F' \
                or (name == 'Return' and event.get_state() & Gdk.ModifierType.MOD1_MASK) \
                or (name.upper() == 'L' and ctrl_pressed) \
                or (name.upper() == 'F5' and not self.c_win_fullscreen):
                self.switch_fullscreen(self.c_win)
            elif name.upper() == 'F' and ctrl_pressed:
                self.switch_fullscreen(self.p_win)
            elif name.upper() == 'Q':
                self.save_and_quit()
            elif name == 'Pause':
                self.switch_pause()
            elif name.upper() == 'R':
                self.reset_timer()

            if self.scribbling_mode:
                if name.upper() == 'Z' and ctrl_pressed:
                    self.pop_scribble()
                elif name == 'Escape':
                    self.switch_scribbling()

            # Some key events are already handled by toggle actions in the
            # presenter window, so we must handle them in the content window
            # only to prevent them from double-firing
            if widget is self.c_win:
                if name.upper() == 'P':
                    self.switch_pause()
                elif name.upper() == 'N':
                    self.switch_mode()
                elif name.upper() == 'A':
                    self.switch_annotations()
                elif name.upper() == 'S':
                    self.swap_screens()
                elif name.upper() == 'F':
                    if ctrl_pressed:
                        self.switch_fullscreen(self.p_win)
                    else:
                        self.switch_fullscreen(self.c_win)
                elif name.upper() == 'G':
                    self.on_label_event(self.eb_cur, True)
                elif name.upper() == 'T':
                    self.on_label_ett_event(self.eb_ett, True)
                elif name.upper() == 'B':
                    self.switch_blanked()
                elif name.upper() == 'H':
                    self.switch_scribbling()
                else:
                    return False

                return True
            else:
                return False

            return True

        elif event.type == Gdk.EventType.SCROLL:

            # send all to spinner if it is active to avoid key problems
            if self.editing_cur and self.spin_cur.on_keypress(widget, event):
                return True

            elif event.direction is Gdk.ScrollDirection.SMOOTH:
                return False
            else:
                adj = self.scrolled_window.get_vadjustment()
                if event.direction == Gdk.ScrollDirection.UP:
                    adj.set_value(adj.get_value() - adj.get_step_increment())
                elif event.direction == Gdk.ScrollDirection.DOWN:
                    adj.set_value(adj.get_value() + adj.get_step_increment())
                else:
                    return False

            return True

        return False


    def on_link(self, widget, event):
        """ Manage events related to hyperlinks.

        :param widget: the widget in which the event occured
        :type  widget: :class:`Gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`Gdk.Event`
        """

        # Where did the event occur?
        if widget is self.p_da_next:
            page = self.doc.next_page()
            if page is None:
                return
        else:
            page = self.doc.current_page()

        # Normalize event coordinates and get link
        x, y = event.get_coords()
        ww, wh = widget.get_allocated_width(), widget.get_allocated_height()
        x2, y2 = x/ww, y/wh
        link = page.get_link_at(x2, y2)

        # Event type?
        if event.type == Gdk.EventType.BUTTON_PRESS:
            if link is not None:
                link.follow()

        elif event.type == Gdk.EventType.MOTION_NOTIFY:
            if link is not None:
                cursor = Gdk.Cursor.new(Gdk.CursorType.HAND2)
                widget.get_window().set_cursor(cursor)
            else:
                widget.get_window().set_cursor(None)

        else:
            print(_("Unknown event {}").format(event.type))


    def on_label_event(self, *args):
        """ Manage events on the current slide label/entry.

        This function replaces the label with an entry when clicked, replaces
        the entry with a label when needed, etc. The nasty stuff it does is an
        ancient kind of dark magic that should be avoided as much as possible...

        :param widget: the widget in which the event occured
        :type  widget: :class:`Gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`Gdk.Event`
        """

        event = args[-1]

        # we can come manually or through a menu action as well
        alt_start_editing = (type(event) == bool and event is True or type(event) == Gtk.Action)
        event_type = None if alt_start_editing else event.type

        # Click in label-mode
        if alt_start_editing or event.type == Gdk.EventType.BUTTON_PRESS: # click
            if self.editing_cur_ett:
                self.restore_current_label_ett()

            if self.label_cur in self.hb_cur:
                # Replace label with entry
                self.hb_cur.remove(self.label_cur)
                self.spin_cur.show()
                self.hb_cur.add(self.spin_cur)
                self.hb_cur.reorder_child(self.spin_cur, 0)
                self.spin_cur.grab_focus()
                self.editing_cur = True

                self.spin_cur.set_value(self.doc.current_page().number()+1)
                self.spin_cur.select_region(0, -1)

            elif self.editing_cur:
                self.spin_cur.grab_focus()

        else:
            # Ignored event - propagate further
            return False

        return True


    def on_label_ett_event(self, *args):
        """ Manage events on the current slide label/entry.

        This function replaces the label with an entry when clicked, replaces
        the entry with a label when needed, etc. The nasty stuff it does is an
        ancient kind of dark magic that should be avoided as much as possible...

        :param widget: the widget in which the event occured
        :type  widget: :class:`gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`gtk.gdk.Event`
        """

        widget = self.eb_ett.get_child()
        event = args[-1]

        # we can come manually or through a menu action as well
        alt_start_editing = (type(event) == bool and event is True or type(event) == Gtk.Action)
        event_type = None if alt_start_editing else event.type

        # Click on the label
        if widget is self.label_ett and (alt_start_editing or event_type == Gdk.EventType.BUTTON_PRESS):
            if self.editing_cur:
                self.spin_cur.cancel()

            # Set entry text
            self.entry_ett.set_text("{:02}:{:02}".format(*divmod(self.est_time, 60)))
            self.entry_ett.select_region(0, -1)

            # Replace label with entry
            self.eb_ett.remove(self.label_ett)
            self.eb_ett.add(self.entry_ett)
            self.entry_ett.show()
            self.entry_ett.grab_focus()
            self.editing_cur_ett = True

        # Key pressed in the entry
        elif widget is self.entry_ett and event_type == Gdk.EventType.KEY_PRESS:
            name = Gdk.keyval_name(event.keyval)

            # Return key --> restore label and goto page
            if name == "Return" or name == "KP_Enter":
                text = self.entry_ett.get_text()
                self.restore_current_label_ett()

                t = ["0" + n.strip() for n in text.split(':')]
                try:
                    m = int(t[0])
                    s = int(t[1])
                except ValueError:
                    print(_("Invalid time (mm or mm:ss expected), got \"{}\"").format(text))
                    return True
                except IndexError:
                    s = 0

                self.est_time = m * 60 + s;
                self.label_ett.set_text("{:02}:{:02}".format(*divmod(self.est_time, 60)))
                self.label_time.override_color(Gtk.StateType.NORMAL, self.label_color_default)
                return True

            # Escape key --> just restore the label
            elif name == "Escape":
                self.restore_current_label_ett()
                return True
            else:
                Gtk.Entry.do_key_press_event(widget, event)

        return True


    def resize_annotation_list(self):
        """ Readjust the annotation list's scroll window
        so it won't compete for space with the slide frame(s) above
        """
        r = self.p_frame_next.props.ratio
        w = self.p_frame_next.props.parent.get_allocated_width()
        h = self.p_frame_next.props.parent.props.parent.get_allocated_height()
        n = 2 if self.notes_mode else 1
        self.annotation_renderer.props.wrap_width = w - 10
        self.p_frame_annot.set_size_request(-1, max(h - n * 1.2 * (w / r), 100))
        self.scrollable_treelist.get_column(0).queue_resize()
        self.scrolled_window.queue_resize()


    def restore_current_label(self):
        """ Make sure that the current page number is displayed in a label and not in an entry.
        If it is an entry, then replace it with the label.
        """
        if self.label_cur not in self.hb_cur:
            self.hb_cur.remove(self.spin_cur)
            self.hb_cur.pack_start(self.label_cur, True, True, 0)
            self.hb_cur.reorder_child(self.label_cur, 0)

        self.editing_cur = False



    def restore_current_label_ett(self):
        """ Make sure that the current page number is displayed in a label and not in an entry.
        If it is an entry, then replace it with the label.
        """
        child = self.eb_ett.get_child()
        if child is not self.label_ett:
            self.eb_ett.remove(child)
            self.eb_ett.add(self.label_ett)

        self.editing_cur_ett = False


    def update_page_numbers(self):
        """ Update the displayed page numbers.
        """

        cur_nb = self.doc.current_page().number()
        cur = str(cur_nb+1)

        self.label_cur.set_text(cur)
        self.restore_current_label()


    def update_time(self):
        """ Update the timer and clock labels.

        :return: ``True`` (to prevent the timer from stopping)
        :rtype: boolean
        """

        # Current time
        clock = time.strftime("%H:%M") #"%H:%M:%S"

        # Time elapsed since the beginning of the presentation
        if not self.paused:
            self.delta = time.time() - self.start_time
        elapsed = "{:02}:{:02}".format(*divmod(int(self.delta), 60))
        if self.paused:
            elapsed += _(" (paused)")

        self.label_time.set_text(elapsed)
        self.label_clock.set_text(clock)

        self.update_color()

        return True


    def calc_color(self, from_color, to_color, position):
        """ Compute the interpolation between two colors.

        :param from_color: the color when position = 0
        :type  from_color: :class:`Gdk.RGBA`
        :param to_color: the color when position = 1
        :type  to_color: :class:`Gdk.RGBA`
        :param position: A floating point value in the interval [0.0, 1.0]
        :type  position: float
        :return: The color that is between from_color and to_color
        :rtype: :class:`Gdk.RGBA`
        """
        color_tuple = lambda color: ( color.red, color.green, color.blue, color.alpha )
        interpolate = lambda start, end: start + (end - start) * position

        return Gdk.RGBA(*map(interpolate, color_tuple(from_color), color_tuple(to_color)))


    def update_color(self):
        """ Update the color of the time label based on how much time is remaining.
        """
        if not self.est_time == 0:
            # Set up colors between which to fade, based on how much time remains (<0 has run out of time).
            # Times are given in seconds, in between two of those timestamps the color will interpolated linearly.
            # Outside of the intervals the closest color will be used.
            colors = {
                 300:self.label_color_default,
                   0:self.label_color_ett_reached,
                -150:self.label_color_ett_info,
                -300:self.label_color_ett_warn
            }
            bounds=list(sorted(colors, reverse=True)[:-1])

            remaining = self.est_time - self.delta
            if remaining >= bounds[0]:
                color = colors[bounds[0]]
            elif remaining <= bounds[-1]:
                color = colors[bounds[-1]]
            else:
                c=1
                while bounds[c] >= remaining:
                    c += 1
                position = (remaining - bounds[c-1]) / (bounds[c] - bounds[c-1])
                color = self.calc_color(colors[bounds[c-1]], colors[bounds[c]], position)

            if color:
                self.label_time.override_color(Gtk.StateType.NORMAL, color)

            if (remaining <= 0 and remaining > -5) or (remaining <= -300 and remaining > -310):
                self.label_time.get_style_context().add_class("time-warn")
            else:
                self.label_time.get_style_context().remove_class("time-warn")


    def switch_pause(self, widget=None, event=None):
        """ Switch the timer between paused mode and running (normal) mode.
        """
        if self.paused:
            self.start_time = time.time() - self.delta
            self.paused = False
        else:
            self.paused = True
        self.update_time()


    def reset_timer(self, widget=None, event=None):
        """ Reset the timer.
        """
        self.start_time = time.time()
        self.delta = 0
        self.update_time()


    def set_screensaver(self, must_disable):
        """ Enable or disable the screensaver.

        :param must_disable: if ``True``, indicates that the screensaver must be
           disabled; otherwise it will be enabled
        :type  must_disable: boolean
        """
        if IS_MAC_OS:
            # On Mac OS X we can use caffeinate to prevent the display from sleeping
            if must_disable:
                if self.dpms_was_enabled == None or self.dpms_was_enabled.poll():
                    self.dpms_was_enabled = subprocess.Popen(['caffeinate', '-d', '-w', str(os.getpid())])
            else:
                if self.dpms_was_enabled and not self.dpms_was_enabled.poll():
                    self.dpms_was_enabled.kill()
                    self.dpms_was_enabled.poll()
                    self.dpms_was_enabled = None

        elif IS_POSIX:
            # On Linux, set screensaver with xdg-screensaver
            # (compatible with xscreensaver, gnome-screensaver and ksaver or whatever)
            cmd = "suspend" if must_disable else "resume"
            status = os.system("xdg-screensaver {} {}".format(cmd, self.c_win.get_window().get_xid()))
            if status != 0:
                print(_("Warning: Could not set screensaver status: got status ")+str(status), file=sys.stderr)

            # Also manage screen blanking via DPMS
            if must_disable:
                # Get current DPMS status
                pipe = os.popen("xset q") # TODO: check if this works on all locales
                dpms_status = "Disabled"
                for line in pipe.readlines():
                    if line.count("DPMS is") > 0:
                        dpms_status = line.split()[-1]
                        break
                pipe.close()

                # Set the new value correctly
                if dpms_status == "Enabled":
                    self.dpms_was_enabled = True
                    status = os.system("xset -dpms")
                    if status != 0:
                        print(_("Warning: Could not disable DPMS screen blanking: got status ")+str(status), file=sys.stderr)
                else:
                    self.dpms_was_enabled = False

            elif self.dpms_was_enabled:
                # Re-enable DPMS
                status = os.system("xset +dpms")
                if status != 0:
                    print(_("Warning: Could not enable DPMS screen blanking: got status ")+str(status), file=sys.stderr)

        elif IS_WINDOWS:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Control Panel\Desktop', 0, winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as key:
                    if must_disable:
                        (value,type) = winreg.QueryValueEx(key, "ScreenSaveActive")
                        assert(type == winreg.REG_SZ)
                        self.dpms_was_enabled = (value == "1")
                        if self.dpms_was_enabled:
                            winreg.SetValueEx(key, "ScreenSaveActive", 0, winreg.REG_SZ, "0")
                    elif self.dpms_was_enabled:
                        winreg.SetValueEx(key, "ScreenSaveActive", 0, winreg.REG_SZ, "1")
            except (OSError, PermissionError):
                print(_("Error: access denied when trying to access screen saver settings in registry!"))
        else:
            print(_("Warning: Unsupported OS: can't enable/disable screensaver"), file=sys.stderr)


    def switch_fullscreen(self, widget=None, event=None):
        """ Switch the Content window to fullscreen (if in normal mode)
        or to normal mode (if fullscreen).

        Screensaver will be disabled when entering fullscreen mode, and enabled
        when leaving fullscreen mode.
        """
        if isinstance(widget, Gtk.Action):
            # Called from menu -> use c_win
            widget = self.c_win
            fullscreen = self.c_win_fullscreen
        elif widget == self.c_win:
            fullscreen = self.c_win_fullscreen
        elif widget == self.p_win:
            fullscreen = self.p_win_fullscreen
        else:
            print (_("Unknow widget {} to be fullscreened, aborting.").format(widget), file=sys.stderr)
            return

        if fullscreen:
            widget.unfullscreen()
        else:
            widget.fullscreen()


    def on_window_state_event(self, widget, event, user_data=None):
        """ Track whether the preview window is maximized.
        """
        if widget.get_name() == self.p_win.get_name():
            self.p_win_maximized = (Gdk.WindowState.MAXIMIZED & event.new_window_state) != 0
            self.p_win_fullscreen = (Gdk.WindowState.FULLSCREEN & event.new_window_state) != 0
        elif widget.get_name() == self.c_win.get_name():
            self.c_win_fullscreen = (Gdk.WindowState.FULLSCREEN & event.new_window_state) != 0
            self.set_screensaver(self.c_win_fullscreen)


    def update_frame_position(self, widget=None, user_data=None):
        """ Callback to preview the frame alignement, called from the spinbutton.
        """
        if widget and user_data:
            self.c_frame.set_property(user_data, widget.get_value())


    def adjust_frame_position(self, widget=None, event=None):
        """ Select how to align the frame on screen.
        """
        win_aspect_ratio = float(self.c_win.get_allocated_width()) / self.c_win.get_allocated_height()

        if win_aspect_ratio <= float(self.c_frame.get_property("ratio")):
            prop = "yalign"
        else:
            prop = "xalign"

        val = self.c_frame.get_property(prop)

        button = Gtk.SpinButton()
        button.set_adjustment(Gtk.Adjustment(lower=0.0, upper=1.0, step_incr=0.01))
        button.set_digits(2)
        button.set_value(val)
        button.connect("value-changed", self.update_frame_position, prop)

        popup = Gtk.Dialog(_("Adjust alignment of slides in projector screen"), self.p_win, 0,
                (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                 Gtk.STOCK_OK, Gtk.ResponseType.OK))

        box = popup.get_content_area()
        box.add(button)
        popup.show_all()
        response = popup.run()
        popup.destroy()

        # revert if we cancelled
        if response == Gtk.ResponseType.CANCEL:
            self.c_frame.set_property(prop, val)
        else:
            self.config.set('content', prop, str(button.get_value()))


    def swap_screens(self, widget=None, event=None):
        """ Swap the monitors on which each window is displayed (if there are 2 monitors at least).
        """
        c_win_was_fullscreen = self.c_win_fullscreen
        p_win_was_fullscreen = self.p_win_fullscreen
        p_win_was_maximized  = self.p_win_maximized
        if c_win_was_fullscreen:
            self.c_win.unfullscreen()
        if p_win_was_fullscreen:
            self.p_win.unfullscreen()
        if p_win_was_maximized:
            self.p_win.unmaximize()

        screen = self.p_win.get_screen()
        if screen.get_n_monitors() > 1:
            # temporarily remove the annotations' list size so it won't hinder p_frame_next size adjustment
            self.scrolled_window.set_size_request(-1,  100)

            # Though Gtk.Window is a Gtk.Widget get_parent_window() actually returns None on self.{c,p}_win
            p_monitor = screen.get_monitor_at_window(self.p_frame_cur.get_parent_window())
            c_monitor = screen.get_monitor_at_window(self.c_frame.get_parent_window())

            if p_monitor == c_monitor:
                return

            p_monitor, c_monitor = (c_monitor, p_monitor)

            cx, cy, cw, ch = self.c_win.get_position() + self.c_win.get_size()
            px, py, pw, ph = self.p_win.get_position() + self.p_win.get_size()

            c_bounds = screen.get_monitor_geometry(c_monitor)
            p_bounds = screen.get_monitor_geometry(p_monitor)
            self.c_win.move(c_bounds.x + (c_bounds.width - cw) / 2, c_bounds.y + (c_bounds.height - ch) / 2)
            self.p_win.move(p_bounds.x + (p_bounds.width - pw) / 2, p_bounds.y + (p_bounds.height - ph) / 2)

            if p_win_was_fullscreen:
                self.p_win.fullscreen()
            elif p_win_was_maximized:
                self.p_win.maximize()

            if c_win_was_fullscreen:
                self.c_win.fullscreen()


    def switch_blanked(self, widget=None, event=None):
        """ Switch the blanked mode of the main screen.
        """
        self.blanked = not self.blanked
        self.c_da.queue_draw()


    def switch_start_blanked(self, widget=None, event=None):
        """ Switch the blanked mode of the main screen.
        """
        if self.config.getboolean('content', 'start_blanked'):
            self.config.set('content', 'start_blanked', 'off')
        else:
            self.config.set('content', 'start_blanked', 'on')


    def switch_start_fullscreen(self, widget=None):
        """ Switch the blanked mode of the main screen.
        """
        name_words=widget.get_name().lower().split()
        if 'content' in name_words:
            target = 'content'
        elif 'presenter' in name_words:
            target = 'presenter'
        else:
            print(_("ERROR Unknown widget to start fullscreen: {}").format(widget.get_name()))
            return

        if self.config.getboolean(target, 'start_fullscreen'):
            self.config.set(target, 'start_fullscreen', 'off')
        else:
            self.config.set(target, 'start_fullscreen', 'on')


    def switch_mode(self, widget=None, event=None):
        """ Switch the display mode to "Notes mode" or "Normal mode" (without notes).
        """
        if self.notes_mode:
            self.notes_mode = False
            self.cache.set_widget_type("c_da", PDF_REGULAR)
            self.cache.set_widget_type("p_da_cur", PDF_REGULAR)
            self.cache.set_widget_type("p_da_next", PDF_REGULAR)
            self.cache.add_widget("scribble_p_da", PDF_REGULAR)
            self.cache.disable_prerender("p_da_pres")
            self.p_frame_cur.set_label(_("Current slide"))
            self.p_frame_pres.set_visible(False)
        else:
            self.notes_mode = True
            self.cache.set_widget_type("c_da", PDF_CONTENT_PAGE)
            self.cache.set_widget_type("p_da_cur", PDF_NOTES_PAGE)
            self.cache.set_widget_type("p_da_next", PDF_CONTENT_PAGE)
            self.cache.add_widget("scribble_p_da", PDF_CONTENT_PAGE)
            self.cache.enable_prerender("p_da_pres")
            self.p_frame_cur.set_label(_("Notes"))
            self.p_frame_pres.set_visible(True)

        # show/hide annotations, in opposite of nodes'
        if self.show_annotations == self.notes_mode:
            self.switch_annotations(widget, event)

        self.on_page_change(False)


    def switch_annotations(self, widget=None, event=None):
        """ Switch the display to show annotations or to hide them.
        """
        if self.show_annotations:
            self.show_annotations = False
            self.p_frame_annot.set_visible(False)
        else:
            self.show_annotations = True
            self.p_frame_annot.set_visible(True)

        self.on_page_change(False)


    def switch_bigbuttons(self, widget=None, event=None):
        """ Toggle the display of big buttons (nice for touch screens)
        """
        self.show_bigbuttons = not self.show_bigbuttons

        self.prev_button.set_visible(self.show_bigbuttons)
        self.next_button.set_visible(self.show_bigbuttons)
        self.highlight_button.set_visible(self.show_bigbuttons)
        self.config.set('presenter', 'show_bigbuttons', 'on' if self.show_bigbuttons else 'off')


    def track_scribble(self, widget=None, event=None):
        """ Track events defining drawings by user, on top of current slide
        """
        if not self.scribbling_mode:
            return False

        if event.get_event_type() is Gdk.EventType.BUTTON_PRESS:
            self.scribble_list.append( (self.scribble_color, self.scribble_width, []) )

        ww, wh = widget.get_allocated_width(), widget.get_allocated_height()
        ex, ey = event.get_coords()
        self.scribble_list[-1][2].append((ex / ww, ey / wh))

        self.scribble_c_da.queue_draw()
        self.scribble_p_da.queue_draw()


    def draw_scribble(self, widget, cairo_context):
        """ Drawings by user
        """
        ww, wh = widget.get_allocated_width(), widget.get_allocated_height()

        if widget is not self.scribble_c_da:
            page = self.doc.current_page()
            nb = page.number()
            pb = self.cache.get("scribble_p_da", nb)

            if pb is None:
                # Cache miss: render the page, and save it to the cache
                pb = widget.get_window().create_similar_surface(cairo.CONTENT_COLOR, ww, wh)
                wtype = PDF_CONTENT_PAGE if self.notes_mode else PDF_REGULAR

                cairo_prerender = cairo.Context(pb)
                page.render_cairo(cairo_prerender, ww, wh, wtype)

                cairo_context.set_source_surface(pb, 0, 0)
                cairo_context.paint()

                self.cache.set("scribble_p_da", nb, pb)
            else:
                # Cache hit: draw the surface from the cache to the widget
                cairo_context.set_source_surface(pb, 0, 0)
                cairo_context.paint()

        cairo_context.set_line_cap(cairo.LINE_CAP_ROUND)

        for color, width, points in self.scribble_list:
            points = [(p[0] * ww, p[1] * wh) for p in points]

            cairo_context.set_source_rgba(*color)
            cairo_context.set_line_width(width)
            cairo_context.move_to(*points[0])

            for p in points[1:]:
                cairo_context.line_to(*p)
            cairo_context.stroke()


    def update_color(self, widget = None):
        """ Callback for the color chooser button, to set scribbling color
        """
        if widget:
            self.scribble_color = widget.get_rgba()
            self.config.set('scribble', 'color', self.scribble_color.to_string())


    def update_width(self, widget = None, event = None, value = None):
        """ Callback for the width chooser slider, to set scribbling width
        """
        if widget:
            self.scribble_width = int(value)
            self.config.set('scribble', 'width', str(self.scribble_width))


    def clear_scribble(self, widget = None):
        """ Callback for the scribble undo button, to undo the last scribble
        """
        del self.scribble_list[:]

        self.scribble_c_da.queue_draw()
        self.scribble_p_da.queue_draw()


    def pop_scribble(self, widget = None):
        """ Callback for the scribble undo button, to undo the last scribble
        """
        if self.scribble_list:
            self.scribble_list.pop()

        self.scribble_c_da.queue_draw()
        self.scribble_p_da.queue_draw()


    def setup_scribbling(self):
        """ Setup all the necessary for scribbling
        """
        self.scribble_color = Gdk.RGBA()
        self.scribble_color.parse(self.config.get('scribble', 'color'))
        self.scribble_width = self.config.getint('scribble', 'width')
        self.cache.add_widget("scribble_p_da", PDF_CONTENT_PAGE if self.notes_mode else PDF_REGULAR, False)

        self.scribble_p_da = Gtk.DrawingArea()
        self.scribble_p_da.set_name("scribble_p_da")
        self.scribble_p_frame = Gtk.AspectFrame(yalign=0, ratio=4./3., obey_child=False)

        self.scribble_p_eb = Gtk.EventBox()
        self.scribble_p_frame.add(self.scribble_p_eb)
        self.scribble_p_eb.add(self.scribble_p_da)

        close = Gtk.Button(stock=Gtk.STOCK_CLOSE)
        clear = Gtk.Button(stock=Gtk.STOCK_CLEAR)
        undo = Gtk.Button(stock=Gtk.STOCK_UNDO)
        color = Gtk.ColorButton()
        width = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 2,30,1)

        color.set_rgba(self.scribble_color)
        color.set_use_alpha(True)
        width.set_value(self.scribble_width)
        width.set_draw_value(False)
        width.set_show_fill_level(False)
        width.set_has_origin(True)

        close.connect("clicked", self.switch_scribbling)
        clear.connect("clicked", self.clear_scribble)
        undo.connect("clicked", self.pop_scribble)
        color.connect("color-set", self.update_color)
        width.connect("change-value", self.update_width)

        close.set_size_request(80, 80)
        clear.set_size_request(80, 80)
        undo.set_size_request(80, 80)
        color.set_size_request(80, 80)
        width.set_size_request(80, 160)

        tools = Gtk.VBox()
        tools.pack_start(close, False, False, 5)
        tools.pack_start(clear, False, False, 5)
        tools.pack_start(undo, False, False, 5)
        tools.pack_start(color, False, False, 5)
        tools.pack_start(width, False, False, 5)

        self.scribble_overlay = Gtk.HBox()
        self.scribble_overlay.set_name("scribble_overlay")
        self.scribble_overlay.pack_start(self.scribble_p_frame, True, True, 0)
        self.scribble_overlay.pack_start(tools, False, True, 0)

        self.scribble_c_da = Gtk.DrawingArea()
        self.scribble_c_da.set_halign(Gtk.Align.FILL)
        self.scribble_c_da.set_valign(Gtk.Align.FILL)

        self.scribble_c_eb = Gtk.EventBox()
        self.scribble_c_eb.add(self.scribble_c_da)
        self.c_overlay.add_overlay(self.scribble_c_eb)
        self.c_overlay.reorder_overlay(self.scribble_c_eb, 1)
        self.scribble_overlay.show_all()

        self.scribble_p_da.connect("draw", self.draw_scribble)
        self.scribble_c_da.connect("draw", self.draw_scribble)

        self.scribble_c_eb.connect("button-press-event", self.track_scribble)
        self.scribble_p_eb.connect("button-press-event", self.track_scribble)

        self.scribble_c_eb.connect("button-release-event", self.track_scribble)
        self.scribble_p_eb.connect("button-release-event", self.track_scribble)

        self.scribble_c_eb.connect("motion-notify-event", self.track_scribble)
        self.scribble_p_eb.connect("motion-notify-event", self.track_scribble)

        self.scribble_p_da.connect("configure-event", self.on_configure_da)


    def switch_scribbling(self, widget=None, event=None):
        """ Starts the mode where one can read on top of the screen
        """

        if self.scribbling_mode:
            self.p_central.remove(self.scribble_overlay)
            self.p_central.pack_start(self.hpaned, True, True, 0)
            self.scribbling_mode = False

        else:
            pr = self.doc.current_page().get_aspect_ratio(self.notes_mode)
            self.scribble_p_frame.set_property('ratio', pr)

            self.p_central.remove(self.hpaned)
            self.p_central.pack_start(self.scribble_overlay, True, True, 0)

            self.p_central.queue_draw()

            self.scribbling_mode = True

            self.cache.resize_widget("scribble_p_da",
                self.scribble_p_da.get_allocated_width(),
                self.scribble_p_da.get_allocated_height())


##
# Local Variables:
# mode: python
# indent-tabs-mode: nil
# py-indent-offset: 4
# fill-column: 80
# end:
