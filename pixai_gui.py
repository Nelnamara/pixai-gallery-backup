#!/usr/bin/env python3
"""
pixai_gui.py  —  PySide6 desktop front-end for pixai_gallery_backup

Requirements:
    pip install PySide6

Run:
    python pixai_gui.py
"""
import io
import json
import sys
import webbrowser
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QProgressBar, QPushButton, QRadioButton, QButtonGroup,
    QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QSizePolicy,
)

try:
    import pixai_gallery_backup as core
except ImportError:
    print("pixai_gallery_backup.py must be in the same folder as this script.")
    sys.exit(1)

try:
    import pixai_gallery as gallery_mod
    _GALLERY_AVAILABLE = True
except ImportError:
    _GALLERY_AVAILABLE = False

SETTINGS_FILE = Path("pixai_gui_settings.json")

# ---------------------------------------------------------------------------
# Dark theme — Catppuccin Mocha palette
# ---------------------------------------------------------------------------
DARK_QSS = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-size: 10pt;
}
QMainWindow {
    background-color: #181825;
}
QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #1e1e2e;
    border-top: none;
}
QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    padding: 7px 20px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    min-width: 90px;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #cba6f7;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background-color: #313244;
    color: #cdd6f4;
}
QGroupBox {
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    color: #89dceb;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 10px;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 8px;
    color: #cdd6f4;
    selection-background-color: #cba6f7;
    selection-color: #1e1e2e;
    min-height: 22px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #cba6f7;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: #585b70;
    border-color: #313244;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #45475a;
    border: none;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #585b70;
}
QPushButton {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 5px 14px;
    color: #cdd6f4;
    min-height: 24px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #cba6f7;
    color: #cba6f7;
}
QPushButton:pressed  { background-color: #585b70; }
QPushButton:disabled { color: #45475a; border-color: #313244; background-color: #1e1e2e; }
QPushButton#btn_start {
    background-color: #a6e3a1; color: #1e1e2e;
    border-color: #a6e3a1; font-weight: bold; min-width: 110px;
}
QPushButton#btn_start:hover    { background-color: #94e2d5; border-color: #94e2d5; }
QPushButton#btn_start:disabled { background-color: #313244; color: #45475a; border-color: #313244; }
QPushButton#btn_stop {
    background-color: #f38ba8; color: #1e1e2e;
    border-color: #f38ba8; font-weight: bold; min-width: 80px;
}
QPushButton#btn_stop:hover    { background-color: #eba0ac; border-color: #eba0ac; }
QPushButton#btn_stop:disabled { background-color: #313244; color: #45475a; border-color: #313244; }
QPushButton#btn_probe {
    background-color: #f9e2af; color: #1e1e2e; border-color: #f9e2af;
}
QPushButton#btn_probe:hover { background-color: #fab387; border-color: #fab387; }
QPushButton#btn_count {
    background-color: #89b4fa; color: #1e1e2e; border-color: #89b4fa;
}
QPushButton#btn_count:hover { background-color: #74c7ec; border-color: #74c7ec; }
QPushButton#btn_run {
    background-color: #cba6f7; color: #1e1e2e;
    border-color: #cba6f7; font-weight: bold; min-width: 120px;
}
QPushButton#btn_run:hover    { background-color: #b4befe; border-color: #b4befe; }
QPushButton#btn_run:disabled { background-color: #313244; color: #45475a; border-color: #313244; }
QTextEdit {
    background-color: #11111b;
    border: 1px solid #313244;
    border-radius: 4px;
    color: #a6e3a1;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 9pt;
}
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid #45475a; border-radius: 3px; background-color: #313244;
}
QCheckBox::indicator:checked  { background-color: #cba6f7; border-color: #cba6f7; }
QCheckBox::indicator:disabled { background-color: #1e1e2e; border-color: #313244; }
QRadioButton { spacing: 6px; }
QRadioButton::indicator {
    width: 14px; height: 14px;
    border: 1px solid #45475a; border-radius: 7px; background-color: #313244;
}
QRadioButton::indicator:checked { background-color: #cba6f7; border-color: #cba6f7; }
QScrollBar:vertical {
    background-color: #181825; width: 10px; margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #45475a; border-radius: 5px; min-height: 24px; margin: 2px;
}
QScrollBar::handle:vertical:hover    { background-color: #585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: #181825; height: 10px;
}
QScrollBar::handle:horizontal {
    background-color: #45475a; border-radius: 5px; min-width: 24px; margin: 2px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #313244; border: 1px solid #45475a; outline: none;
    selection-background-color: #cba6f7; selection-color: #1e1e2e;
}
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #313244; }
"""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_settings():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(data):
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), "utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------

class FolderRow(QWidget):
    """QLineEdit + Browse button for picking a directory."""

    def __init__(self, placeholder="", default="", parent=None):
        super().__init__(parent)
        self._edit = QLineEdit(default)
        self._edit.setPlaceholderText(placeholder)
        btn = QPushButton("Browse…")
        btn.setFixedWidth(80)
        btn.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._edit)
        lay.addWidget(btn)

    def _browse(self):
        start = self._edit.text().strip() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select folder", start)
        if d:
            self._edit.setText(d)

    @property
    def path(self):
        return self._edit.text().strip()

    @path.setter
    def path(self, v):
        self._edit.setText(str(v))


class LogWidget(QTextEdit):
    """Read-only auto-scrolling monospace log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.NoWrap)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def append_line(self, text):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text + "\n")
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def clear_log(self):
        self.clear()


def _make_progress_row():
    """Return (QHBoxLayout, QProgressBar, QLabel) for a standard progress row."""
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(14)
    lbl = QLabel("Ready")
    lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
    row = QHBoxLayout()
    row.addWidget(bar, stretch=1)
    row.addWidget(lbl)
    return row, bar, lbl


class _LogStream(io.RawIOBase):
    """Write-only stream that calls emit_fn for each completed line."""

    def __init__(self, emit_fn):
        super().__init__()
        self._emit = emit_fn
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self):
        if self._buf:
            self._emit(self._buf)
            self._buf = ""


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class Worker(QThread):
    """Run fn(*args) in a background thread, capturing stdout → log signal."""

    log = Signal(str)
    done = Signal(bool, str)       # (success, error_message)
    progress = Signal(int, int, int)  # (done, total, new_downloads)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self):
        stream = _LogStream(self.log.emit)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = stream
        sys.stderr = stream
        try:
            self._fn(*self._args)
            self.done.emit(True, "")
        except core.PixAIError as e:
            self.done.emit(False, str(e))
        except SystemExit as e:
            msg = str(e.code) if e.code not in (None, 0) else ""
            self.done.emit(e.code in (None, 0), msg)
        except Exception:
            import traceback
            self.done.emit(False, traceback.format_exc())
        finally:
            stream.flush()
            sys.stdout, sys.stderr = old_out, old_err


# ---------------------------------------------------------------------------
# Shared settings bar  (token + output folder, always visible)
# ---------------------------------------------------------------------------

class SettingsBar(QGroupBox):

    def __init__(self, settings, parent=None):
        super().__init__("Connection & Output", parent)

        lbl_tok = QLabel("Token:")
        lbl_tok.setFixedWidth(60)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText(
            "Bearer JWT  (or set PIXAI_TOKEN env var / create token.txt)")
        self.token_edit.setText(settings.get("token", ""))

        eye = QPushButton("👁")
        eye.setFixedWidth(34)
        eye.setCheckable(True)
        eye.setToolTip("Show / hide token")
        eye.toggled.connect(
            lambda v: self.token_edit.setEchoMode(
                QLineEdit.Normal if v else QLineEdit.Password))

        load_btn = QPushButton("Load token.txt")
        load_btn.setFixedWidth(110)
        load_btn.clicked.connect(self._load_token_file)

        tok_row = QHBoxLayout()
        tok_row.addWidget(lbl_tok)
        tok_row.addWidget(self.token_edit)
        tok_row.addWidget(eye)
        tok_row.addWidget(load_btn)

        lbl_out = QLabel("Output:")
        lbl_out.setFixedWidth(60)
        _default_out = str(Path(__file__).parent / "pixai_backup")
        self.out_folder = FolderRow(
            placeholder=_default_out,
            default=settings.get("out", _default_out),
        )

        # Auto-load token.txt from script folder if token field is empty
        if not self.token_edit.text().strip():
            _tok_path = Path(__file__).parent / "token.txt"
            if _tok_path.exists():
                self.token_edit.setText(_tok_path.read_text("utf-8").strip())

        out_row = QHBoxLayout()
        out_row.addWidget(lbl_out)
        out_row.addWidget(self.out_folder)

        lay = QVBoxLayout(self)
        lay.addLayout(tok_row)
        lay.addLayout(out_row)

    def _load_token_file(self):
        # If token.txt exists next to the script, load it directly
        default_tok = Path(__file__).parent / "token.txt"
        if default_tok.exists():
            self.token_edit.setText(default_tok.read_text("utf-8").strip())
            return
        # Otherwise open a file picker
        start_dir = str(Path(__file__).parent)
        p, _ = QFileDialog.getOpenFileName(
            self, "Load token file", start_dir, "Text files (*.txt);;All files (*)")
        if p:
            self.token_edit.setText(Path(p).read_text("utf-8").strip())

    @property
    def token(self):
        return self.token_edit.text().strip() or None

    @property
    def out(self):
        return self.out_folder.path or str(Path(__file__).parent / "pixai_backup")


# ---------------------------------------------------------------------------
# Download tab
# ---------------------------------------------------------------------------

class DownloadTab(QWidget):

    def __init__(self, settings_bar, settings, parent=None):
        super().__init__(parent)
        self._bar = settings_bar
        self._worker = None

        opts = QGroupBox("Download Options")
        g = QVBoxLayout(opts)

        # Row 1: page size, max tasks, delay
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Page size:"))
        self.page_size = QSpinBox()
        self.page_size.setRange(1, 8000)
        self.page_size.setValue(settings.get("page_size", 250))
        self.page_size.setFixedWidth(70)
        r1.addWidget(self.page_size)
        r1.addSpacing(16)
        r1.addWidget(QLabel("Workers:"))
        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.workers.setValue(settings.get("workers", 4))
        self.workers.setFixedWidth(55)
        self.workers.setToolTip("Parallel download workers. 1 = serial/polite; "
                                "higher saturates bandwidth on bulk pulls.")
        r1.addWidget(self.workers)
        r1.addSpacing(16)
        r1.addWidget(QLabel("Max tasks (0=all):"))
        self.max_tasks = QSpinBox()
        self.max_tasks.setRange(0, 999999)
        self.max_tasks.setValue(settings.get("max_tasks", 0))
        self.max_tasks.setFixedWidth(80)
        r1.addWidget(self.max_tasks)
        r1.addSpacing(16)
        r1.addWidget(QLabel("Delay (s):"))
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.0, 30.0)
        self.delay.setSingleStep(0.1)
        self.delay.setDecimals(1)
        self.delay.setValue(settings.get("delay", 0.4))
        self.delay.setFixedWidth(65)
        r1.addWidget(self.delay)
        r1.addStretch()
        g.addLayout(r1)

        # Row 2: name length, separator
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Name length:"))
        self.name_len = QSpinBox()
        self.name_len.setRange(10, 200)
        self.name_len.setValue(settings.get("name_length", 60))
        self.name_len.setFixedWidth(65)
        r2.addWidget(self.name_len)
        r2.addSpacing(16)
        r2.addWidget(QLabel("Separator:"))
        self.name_sep = QComboBox()
        self.name_sep.addItems(["_", "-"])
        self.name_sep.setCurrentText(settings.get("name_sep", "_"))
        self.name_sep.setFixedWidth(50)
        r2.addWidget(self.name_sep)
        r2.addStretch()
        g.addLayout(r2)

        # Row 3: live organize mode
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Organize:"))
        self._org_grp = QButtonGroup(self)
        self.org_flat     = QRadioButton("Flat (images/ folder)")
        self.org_live     = QRadioButton("Prompt naming (live)")
        self.org_adv_live = QRadioButton("Batch + Month folders (live)")
        for rb in (self.org_flat, self.org_live, self.org_adv_live):
            self._org_grp.addButton(rb)
            r3.addWidget(rb)
        mode = settings.get("org_mode", "flat")
        (self.org_adv_live if mode == "adv_live" else
         self.org_live if mode == "live" else self.org_flat).setChecked(True)
        r3.addStretch()
        g.addLayout(r3)

        # Row 4: convert on download + JPEG options
        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Convert:"))
        self.convert_combo = QComboBox()
        self.convert_combo.addItem("None (keep original)", None)
        self.convert_combo.addItem("→ PNG", "png")
        self.convert_combo.addItem("→ JPEG", "jpeg")
        idx = self.convert_combo.findData(settings.get("convert", None))
        self.convert_combo.setCurrentIndex(max(0, idx))
        self.convert_combo.setFixedWidth(150)
        r4.addWidget(self.convert_combo)
        r4.addSpacing(12)
        self._lbl_jq = QLabel("JPEG quality:")
        r4.addWidget(self._lbl_jq)
        self.jpeg_qual = QSpinBox()
        self.jpeg_qual.setRange(1, 100)
        self.jpeg_qual.setValue(settings.get("jpeg_quality", 92))
        self.jpeg_qual.setFixedWidth(55)
        r4.addWidget(self.jpeg_qual)
        r4.addSpacing(8)
        self._lbl_bg = QLabel("BG:")
        r4.addWidget(self._lbl_bg)
        self.jpeg_bg = QComboBox()
        self.jpeg_bg.addItems(["white", "black"])
        self.jpeg_bg.setCurrentText(settings.get("jpeg_bg", "white"))
        self.jpeg_bg.setFixedWidth(70)
        r4.addWidget(self.jpeg_bg)
        r4.addSpacing(10)
        self.keep_webp = QCheckBox("Keep .webp")
        self.keep_webp.setChecked(settings.get("keep_webp", False))
        r4.addWidget(self.keep_webp)
        r4.addStretch()
        g.addLayout(r4)

        self.convert_combo.currentIndexChanged.connect(self._on_convert_change)
        self._on_convert_change()

        # Full meta row
        r5 = QHBoxLayout()
        self.full_meta = QCheckBox("Fetch full prompt / seed / model  (--full-meta, requires TASK_DETAIL_HASH in config.json)")
        self.full_meta.setChecked(settings.get("full_meta", False))
        r5.addWidget(self.full_meta)
        r5.addStretch()
        g.addLayout(r5)

        # Collect-only row
        r6 = QHBoxLayout()
        self.collect_only = QCheckBox("Collect only — catalog tasks without downloading images  (--collect-only)")
        self.collect_only.setChecked(settings.get("collect_only", False))
        r6.addWidget(self.collect_only)
        r6.addStretch()
        g.addLayout(r6)

        # Incremental update row
        r7 = QHBoxLayout()
        self.update_mode = QCheckBox("Update mode — stop early once new items are caught up  (--update, faster follow-ups)")
        self.update_mode.setChecked(settings.get("update_mode", False))
        r7.addWidget(self.update_mode)
        r7.addStretch()
        g.addLayout(r7)

        # Buttons
        self.btn_start = QPushButton("▶  Start Download")
        self.btn_start.setObjectName("btn_start")
        self.btn_probe = QPushButton("Probe API")
        self.btn_probe.setObjectName("btn_probe")
        self.btn_count = QPushButton("Count Library")
        self.btn_count.setObjectName("btn_count")
        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_probe)
        btn_row.addWidget(self.btn_count)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_stop)

        self.btn_start.clicked.connect(self._start_download)
        self.btn_probe.clicked.connect(self._start_probe)
        self.btn_count.clicked.connect(self._start_count)
        self.btn_stop.clicked.connect(self._stop)

        self.log = LogWidget()

        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setFixedHeight(14)
        self.prog_label = QLabel("Ready")
        self.prog_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        prog_row = QHBoxLayout()
        prog_row.addWidget(self.prog_bar, stretch=1)
        prog_row.addWidget(self.prog_label)

        lay = QVBoxLayout(self)
        lay.addWidget(opts)
        lay.addLayout(btn_row)
        lay.addLayout(prog_row)
        lay.addWidget(self.log, stretch=1)

    def _on_convert_change(self):
        jpeg = self.convert_combo.currentData() in ("jpeg", "jpg")
        for w in (self._lbl_jq, self.jpeg_qual, self._lbl_bg,
                  self.jpeg_bg, self.keep_webp):
            w.setEnabled(jpeg)

    def _build_args(self):
        return SimpleNamespace(
            token=self._bar.token,
            out=self._bar.out,
            page_size=self.page_size.value(),
            max=self.max_tasks.value(),
            delay=self.delay.value(),
            name_length=self.name_len.value(),
            name_sep=self.name_sep.currentText(),
            organize_live=self.org_live.isChecked(),
            organize_adv_live=self.org_adv_live.isChecked(),
            convert=self.convert_combo.currentData(),
            jpeg_quality=self.jpeg_qual.value(),
            jpeg_bg=self.jpeg_bg.currentText(),
            keep_webp=self.keep_webp.isChecked(),
            collect_only=self.collect_only.isChecked(),
            full_meta=self.full_meta.isChecked(),
            update=self.update_mode.isChecked(),
            update_grace=2,
            accurate_count=False,
            workers=self.workers.value(),
            count_page_size=5000,
        )

    def _run(self, fn, *args):
        if self._worker and self._worker.isRunning():
            return
        self.log.clear_log()
        self._set_running(True)
        self._worker = Worker(fn, *args)
        self._worker.log.connect(self.log.append_line)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _start_download(self):
        if self._worker and self._worker.isRunning():
            return
        args = self._build_args()
        self.log.clear_log()
        self.prog_bar.setValue(0)
        self.prog_bar.setRange(0, 100)
        self.prog_label.setText("Counting library...")
        self._set_running(True)
        self._worker = Worker(core.run_download, args)
        # Inject progress callback BEFORE start so the worker thread sees it
        args.progress = self._worker.progress.emit
        self._worker.log.connect(self.log.append_line)
        self._worker.progress.connect(self._update_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _update_progress(self, done, total, new=0):
        new_str = "  (+{} new)".format(new) if new else ""
        if total:
            self.prog_bar.setRange(0, total)
            self.prog_bar.setValue(done)
            if new == 0 and done > 0:
                self.prog_label.setText("Resuming — {}/{} already done".format(done, total))
            elif new > 0:
                self.prog_label.setText("Checked {}/{}{}".format(done, total, new_str))
            else:
                self.prog_label.setText("Counting library...")
        else:
            self.prog_bar.setRange(0, 0)  # indeterminate bounce
            self.prog_label.setText("Checking {}{}...".format(done, new_str))

    def _start_probe(self):    self._run(core.run_probe,    self._build_args())
    def _start_count(self):    self._run(core.run_count,    self._build_args())

    def _stop(self):
        if self._worker:
            self._worker.terminate()
            self._worker.wait(2000)
            self.log.append_line("\n[Stopped by user]")
            self.prog_label.setText("Stopped")
            self._set_running(False)

    def _on_done(self, success, msg):
        self._set_running(False)
        if success:
            self.prog_label.setText("Complete")
        else:
            self.prog_label.setText("Error")
            if msg:
                self.log.append_line("\n[ERROR] " + msg)

    def _set_running(self, running):
        for w in (self.btn_start, self.btn_probe, self.btn_count):
            w.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def collect_settings(self):
        return {
            "page_size":    self.page_size.value(),
            "max_tasks":    self.max_tasks.value(),
            "delay":        self.delay.value(),
            "name_length":  self.name_len.value(),
            "name_sep":     self.name_sep.currentText(),
            "org_mode":     ("adv_live" if self.org_adv_live.isChecked()
                             else "live" if self.org_live.isChecked() else "flat"),
            "convert":      self.convert_combo.currentData(),
            "jpeg_quality": self.jpeg_qual.value(),
            "jpeg_bg":      self.jpeg_bg.currentText(),
            "keep_webp":    self.keep_webp.isChecked(),
            "full_meta":    self.full_meta.isChecked(),
            "collect_only": self.collect_only.isChecked(),
            "update_mode":  self.update_mode.isChecked(),
            "workers":      self.workers.value(),
        }


# ---------------------------------------------------------------------------
# Organize tab
# ---------------------------------------------------------------------------

class OrganizeTab(QWidget):

    def __init__(self, settings_bar, settings, parent=None):
        super().__init__(parent)
        self._bar = settings_bar
        self._worker = None

        opts = QGroupBox("Organize Options")
        g = QVBoxLayout(opts)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Mode:"))
        self._mode_grp = QButtonGroup(self)
        self.rb_simple = QRadioButton("Simple rename  (prompt_taskid_mediaid)")
        self.rb_adv    = QRadioButton("Advanced  (batch/ + YYYY-MM/ folders + metadata)")
        for rb in (self.rb_simple, self.rb_adv):
            self._mode_grp.addButton(rb)
            r1.addWidget(rb)
        (self.rb_adv if settings.get("org_adv", False)
         else self.rb_simple).setChecked(True)
        r1.addStretch()
        g.addLayout(r1)

        r2 = QHBoxLayout()
        self.dry_run = QCheckBox("Dry run (preview only)")
        self.dry_run.setChecked(settings.get("org_dry_run", False))
        r2.addWidget(self.dry_run)
        r2.addSpacing(20)
        r2.addWidget(QLabel("Name length:"))
        self.name_len = QSpinBox()
        self.name_len.setRange(10, 200)
        self.name_len.setValue(settings.get("name_length", 60))
        self.name_len.setFixedWidth(65)
        r2.addWidget(self.name_len)
        r2.addSpacing(10)
        r2.addWidget(QLabel("Sep:"))
        self.name_sep = QComboBox()
        self.name_sep.addItems(["_", "-"])
        self.name_sep.setCurrentText(settings.get("name_sep", "_"))
        self.name_sep.setFixedWidth(50)
        r2.addWidget(self.name_sep)
        r2.addStretch()
        g.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Convert during organize:"))
        self.convert_combo = QComboBox()
        self.convert_combo.addItem("None", None)
        self.convert_combo.addItem("→ PNG", "png")
        self.convert_combo.addItem("→ JPEG", "jpeg")
        self.convert_combo.setFixedWidth(110)
        r3.addWidget(self.convert_combo)
        r3.addSpacing(10)
        self.keep_webp = QCheckBox("Keep .webp")
        r3.addWidget(self.keep_webp)
        r3.addSpacing(10)
        r3.addWidget(QLabel("JPEG quality:"))
        self.jpeg_qual = QSpinBox()
        self.jpeg_qual.setRange(1, 100)
        self.jpeg_qual.setValue(92)
        self.jpeg_qual.setFixedWidth(55)
        r3.addWidget(self.jpeg_qual)
        r3.addStretch()
        g.addLayout(r3)

        self.btn_run = QPushButton("▶  Run Organize")
        self.btn_run.setObjectName("btn_run")
        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_stop)
        self.btn_run.clicked.connect(self._run_organize)
        self.btn_stop.clicked.connect(self._stop)

        prog_row, self.prog_bar, self.prog_label = _make_progress_row()
        self.log = LogWidget()

        lay = QVBoxLayout(self)
        lay.addWidget(opts)
        lay.addLayout(btn_row)
        lay.addLayout(prog_row)
        lay.addWidget(self.log, stretch=1)

    def _build_args(self):
        return SimpleNamespace(
            out=self._bar.out,
            dry_run=self.dry_run.isChecked(),
            name_length=self.name_len.value(),
            name_sep=self.name_sep.currentText(),
            convert=self.convert_combo.currentData(),
            jpeg_quality=self.jpeg_qual.value(),
            jpeg_bg="white",
            keep_webp=self.keep_webp.isChecked(),
        )

    def _run_organize(self):
        if self._worker and self._worker.isRunning():
            return
        self.log.clear_log()
        self.prog_bar.setRange(0, 0)
        self.prog_bar.setValue(0)
        self.prog_label.setText("Working...")
        args = self._build_args()
        out = Path(args.out)
        img_dir = out / "images"
        db_path = out / "catalog.db"
        if self.rb_adv.isChecked():
            fn = lambda: core.cmd_organize(args, out, img_dir, db_path)
        else:
            fn = lambda: core.cmd_rename(args, out, img_dir, db_path)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._worker = Worker(fn)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)
        self._worker.log.connect(self.log.append_line)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.terminate()
            self._worker.wait(2000)
            self.log.append_line("\n[Stopped by user]")
            self._on_done(False, "")

    def _update_progress(self, done, total, _=0):
        if total > 0:
            self.prog_bar.setRange(0, total)
            self.prog_bar.setValue(done)
            self.prog_label.setText("{:,} / {:,} files".format(done, total))
        else:
            self.prog_bar.setRange(0, 0)

    def _on_done(self, success, msg):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.prog_bar.setRange(0, 1)
        self.prog_bar.setValue(1 if success else 0)
        self.prog_label.setText("Complete" if success else ("Error" if msg else "Stopped"))
        if not success and msg:
            self.log.append_line("\n[ERROR] " + msg)

    def collect_settings(self):
        return {
            "org_adv":     self.rb_adv.isChecked(),
            "org_dry_run": self.dry_run.isChecked(),
        }


# ---------------------------------------------------------------------------
# Convert tab
# ---------------------------------------------------------------------------

class ConvertTab(QWidget):

    def __init__(self, settings_bar, settings, parent=None):
        super().__init__(parent)
        self._bar = settings_bar
        self._worker = None

        opts = QGroupBox("Convert Existing .webp Files")
        g = QVBoxLayout(opts)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Target format:"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("PNG", "png")
        self.fmt_combo.addItem("JPEG", "jpeg")
        idx = self.fmt_combo.findData(settings.get("conv_fmt", "png"))
        self.fmt_combo.setCurrentIndex(max(0, idx))
        self.fmt_combo.setFixedWidth(90)
        r1.addWidget(self.fmt_combo)
        r1.addSpacing(16)
        self._lbl_jq = QLabel("JPEG quality:")
        r1.addWidget(self._lbl_jq)
        self.jpeg_qual = QSpinBox()
        self.jpeg_qual.setRange(1, 100)
        self.jpeg_qual.setValue(settings.get("jpeg_quality", 92))
        self.jpeg_qual.setFixedWidth(55)
        r1.addWidget(self.jpeg_qual)
        r1.addSpacing(8)
        self._lbl_bg = QLabel("BG:")
        r1.addWidget(self._lbl_bg)
        self.jpeg_bg = QComboBox()
        self.jpeg_bg.addItems(["white", "black"])
        self.jpeg_bg.setCurrentText(settings.get("jpeg_bg", "white"))
        self.jpeg_bg.setFixedWidth(70)
        r1.addWidget(self.jpeg_bg)
        r1.addStretch()
        g.addLayout(r1)

        r2 = QHBoxLayout()
        self.keep_webp = QCheckBox("Keep original .webp alongside converted file")
        self.keep_webp.setChecked(settings.get("conv_keep_webp", False))
        r2.addWidget(self.keep_webp)
        r2.addSpacing(20)
        self.dry_run = QCheckBox("Dry run (preview only)")
        r2.addWidget(self.dry_run)
        r2.addStretch()
        g.addLayout(r2)

        self.btn_run = QPushButton("▶  Convert Existing .webp")
        self.btn_run.setObjectName("btn_run")
        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_stop)
        self.btn_run.clicked.connect(self._run_convert)
        self.btn_stop.clicked.connect(self._stop)

        prog_row, self.prog_bar, self.prog_label = _make_progress_row()
        self.log = LogWidget()

        lay = QVBoxLayout(self)
        lay.addWidget(opts)
        lay.addLayout(btn_row)
        lay.addLayout(prog_row)
        lay.addWidget(self.log, stretch=1)

        self.fmt_combo.currentIndexChanged.connect(self._on_fmt_change)
        self._on_fmt_change()

    def _on_fmt_change(self):
        jpeg = self.fmt_combo.currentData() in ("jpeg", "jpg")
        for w in (self._lbl_jq, self.jpeg_qual, self._lbl_bg, self.jpeg_bg):
            w.setEnabled(jpeg)

    def _build_args(self):
        return SimpleNamespace(
            out=self._bar.out,
            convert=self.fmt_combo.currentData(),
            jpeg_quality=self.jpeg_qual.value(),
            jpeg_bg=self.jpeg_bg.currentText(),
            keep_webp=self.keep_webp.isChecked(),
            dry_run=self.dry_run.isChecked(),
        )

    def _run_convert(self):
        if self._worker and self._worker.isRunning():
            return
        self.log.clear_log()
        self.prog_bar.setRange(0, 0)
        self.prog_bar.setValue(0)
        self.prog_label.setText("Scanning...")
        args = self._build_args()
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._worker = Worker(core.cmd_convert_existing, args, Path(args.out))
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)
        self._worker.log.connect(self.log.append_line)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _update_progress(self, done, total, _=0):
        if total > 0:
            self.prog_bar.setRange(0, total)
            self.prog_bar.setValue(done)
            self.prog_label.setText("{:,} / {:,} files".format(done, total))
        else:
            self.prog_bar.setRange(0, 0)

    def _stop(self):
        if self._worker:
            self._worker.terminate()
            self._worker.wait(2000)
            self.log.append_line("\n[Stopped by user]")
            self._on_done(False, "")

    def _on_done(self, success, msg):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.prog_bar.setRange(0, 1)
        self.prog_bar.setValue(1 if success else 0)
        self.prog_label.setText("Complete" if success else ("Error" if msg else "Stopped"))
        if not success and msg:
            self.log.append_line("\n[ERROR] " + msg)

    def collect_settings(self):
        return {
            "conv_fmt":      self.fmt_combo.currentData(),
            "conv_keep_webp": self.keep_webp.isChecked(),
        }


# ---------------------------------------------------------------------------
# Utilities tab
# ---------------------------------------------------------------------------

class UtilitiesTab(QWidget):

    def __init__(self, settings_bar, settings, parent=None):
        super().__init__(parent)
        self._bar = settings_bar
        self._worker = None

        def _info(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 2px 0;")
            return lbl

        self.btn_probe = QPushButton("▶  Probe API")
        self.btn_probe.setObjectName("btn_probe")
        self.btn_count = QPushButton("▶  Count Library")
        self.btn_count.setObjectName("btn_count")
        self.btn_stats = QPushButton("▶  Catalog Stats")
        self.btn_stats.setObjectName("btn_run")
        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_probe)
        btn_row.addWidget(self.btn_count)
        btn_row.addWidget(self.btn_stats)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_stop)

        self.btn_probe.clicked.connect(self._run_probe)
        self.btn_count.clicked.connect(self._run_count)
        self.btn_stats.clicked.connect(self._run_stats)
        self.btn_stop.clicked.connect(self._stop)

        self.btn_backfill = QPushButton("▶  Backfill url/width/height")
        self.btn_backfill.setObjectName("btn_run")
        self.btn_backfill_full = QPushButton("▶  Backfill Full Meta")
        self.btn_backfill_full.setObjectName("btn_run")
        self.btn_export_csv = QPushButton("▶  Export CSV")
        self.btn_export_csv.setObjectName("btn_run")
        self.btn_export_csv.setToolTip("Export catalog.db to catalog_export.csv in your output folder")
        self.btn_sync_artworks = QPushButton("▶  Sync Artworks")
        self.btn_sync_artworks.setObjectName("btn_run")
        self.btn_sync_artworks.setToolTip("Fetch your published-artwork metadata (title, NSFW flag, "
                                          "likes, comments, tags) and merge it onto catalog rows by media_id")
        self.btn_fix_models = QPushButton("▶  Fix Model Names")
        self.btn_fix_models.setObjectName("btn_run")
        self.btn_fix_models.setToolTip("Re-resolve readable model names for rows showing a raw "
                                       "numeric id (one API call per distinct model)")
        self.btn_account = QPushButton("▶  Account Info")
        self.btn_account.setObjectName("btn_run")
        self.btn_account.setToolTip("Show your PixAI quota/credits and membership")

        backfill_row = QHBoxLayout()
        backfill_row.addWidget(self.btn_backfill)
        backfill_row.addWidget(self.btn_backfill_full)
        backfill_row.addStretch()

        export_row = QHBoxLayout()
        export_row.addWidget(self.btn_export_csv)
        export_row.addWidget(self.btn_sync_artworks)
        export_row.addWidget(self.btn_fix_models)
        export_row.addWidget(self.btn_account)
        export_row.addStretch()

        self.btn_backfill.clicked.connect(self._run_backfill)
        self.btn_backfill_full.clicked.connect(self._run_backfill_full)
        self.btn_export_csv.clicked.connect(self._run_export_csv)
        self.btn_sync_artworks.clicked.connect(self._run_sync_artworks)
        self.btn_fix_models.clicked.connect(self._run_fix_models)
        self.btn_account.clicked.connect(lambda: self._run(core.run_account_info, self._base_args()))

        # ---- Duplicate audit / dedup ----
        self.btn_audit = QPushButton("▶  Audit Duplicates")
        self.btn_audit.setObjectName("btn_run")
        self.btn_audit.setToolTip("Read-only scan of the whole backup folder; "
                                  "writes audit_report.csv. Changes nothing.")
        self.btn_dedup = QPushButton("▶  Dedup")
        self.btn_dedup.setObjectName("btn_run")
        self.btn_verify = QPushButton("▶  Verify Quarantine")
        self.btn_verify.setObjectName("btn_run")
        self.btn_verify.setToolTip("After dedup: confirm every file in _duplicates/ is "
                                   "redundant (pixel-identical to a kept copy) before you delete it.")
        self.chk_dedup_dry = QCheckBox("Dry run (preview)")
        self.chk_dedup_dry.setChecked(True)
        self.chk_dedup_dry.setToolTip("Preview only. Uncheck to actually move/delete.")
        self.chk_dedup_delete = QCheckBox("Delete (not quarantine)")
        self.chk_dedup_delete.setToolTip("Permanently delete instead of moving to _duplicates/.")
        self.chk_no_content = QCheckBox("Skip content hash")
        self.chk_no_content.setToolTip("Faster: only same-media_id location dupes (Class A), "
                                       "skip byte-identical content dupes (Class B).")

        audit_row = QHBoxLayout()
        audit_row.addWidget(self.btn_audit)
        audit_row.addWidget(self.btn_dedup)
        audit_row.addWidget(self.btn_verify)
        audit_row.addWidget(self.chk_dedup_dry)
        audit_row.addWidget(self.chk_dedup_delete)
        audit_row.addWidget(self.chk_no_content)
        audit_row.addStretch()

        self.btn_audit.clicked.connect(self._run_audit)
        self.btn_dedup.clicked.connect(self._run_dedup)
        self.btn_verify.clicked.connect(self._run_verify)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("API delay (s):"))
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.0, 30.0)
        self.delay.setSingleStep(0.1)
        self.delay.setDecimals(1)
        self.delay.setValue(settings.get("util_delay", 0.4))
        self.delay.setFixedWidth(65)
        delay_row.addWidget(self.delay)
        delay_row.addStretch()

        self.log = LogWidget()

        lay = QVBoxLayout(self)
        lay.addWidget(_info(
            "Probe — fetches the newest page and resolves the full-res URL for the "
            "first task.  Good sanity-check before a full download."))
        lay.addWidget(_info(
            "Count — pages through your entire history and tallies tasks and images "
            "without downloading anything."))
        lay.addWidget(_info(
            "Catalog Stats — reads catalog.db and reports download / "
            "pending / missing counts."))
        lay.addLayout(btn_row)
        lay.addWidget(_info(
            "Backfill url/width/height — resolves the media URL and dimensions for "
            "catalog rows that are missing them (uses resolve_media; token required)."))
        lay.addWidget(_info(
            "Backfill Full Meta — fetches the full prompt, seed, steps, sampler, "
            "CFG, and model name for rows missing them via getTaskById "
            "(requires TASK_DETAIL_HASH in config.json; also fills url/width/height)."))
        lay.addLayout(backfill_row)
        lay.addWidget(_info(
            "Export CSV — saves a copy of catalog.db as catalog_export.csv "
            "in your output folder (useful for spreadsheets or backup)."))
        lay.addLayout(export_row)
        lay.addWidget(_info(
            "Audit Duplicates — read-only scan of the whole backup folder for "
            "duplicate images (same media_id across folders, plus byte-identical "
            "copies). Writes audit_report.csv. Dedup — removes the redundant copies, "
            "keeping the most-organized one; quarantines to _duplicates/ by default. "
            "Leave Dry run checked to preview first."))
        lay.addLayout(audit_row)
        lay.addLayout(delay_row)

        prog_row, self.prog_bar, self.prog_label = _make_progress_row()
        lay.addLayout(prog_row)
        lay.addWidget(self.log, stretch=1)

    def _base_args(self):
        return SimpleNamespace(
            token=self._bar.token,
            out=self._bar.out,
            page_size=20,
            delay=self.delay.value(),
            count_page_size=5000,
        )

    def collect_settings(self):
        return {"util_delay": self.delay.value()}

    def _run(self, fn, *args):
        if self._worker and self._worker.isRunning():
            return
        self.log.clear_log()
        self._set_running(True)
        self._worker = Worker(fn, *args)
        self._worker.log.connect(self.log.append_line)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _run_probe(self):   self._run(core.run_probe,         self._base_args())
    def _run_count(self):   self._run(core.run_count,         self._base_args())
    def _run_stats(self):   self._run(core.run_catalog_stats, self._base_args())

    def _run_backfill(self):
        args = self._base_args()
        self._run(core.run_backfill_meta, args)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)

    def _run_backfill_full(self):
        args = self._base_args()
        self._run(core.run_backfill_full_meta, args)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)

    def _run_audit(self):
        args = self._base_args()
        args.no_content = self.chk_no_content.isChecked()
        self._run(core.run_audit, args)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)

    def _run_dedup(self):
        args = self._base_args()
        args.no_content = self.chk_no_content.isChecked()
        args.apply = not self.chk_dedup_dry.isChecked()
        args.dedup_delete = self.chk_dedup_delete.isChecked()
        self._run(core.run_dedup, args)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)

    def _run_verify(self):
        args = self._base_args()
        args.restore_orphans = False
        self._run(core.run_verify_dupes, args)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)

    def _run_sync_artworks(self):
        self._run(core.run_sync_artworks, self._base_args())

    def _run_fix_models(self):
        args = self._base_args()
        args.relabel_removed = True  # clean menus: removed ids -> "Unknown or removed model"
        self._run(core.run_fix_models, args)
        args.progress = self._worker.progress.emit
        self._worker.progress.connect(self._update_progress)

    def _run_export_csv(self):
        out = Path(self._bar.out)
        db_path = out / "catalog.db"
        csv_path = out / "catalog_export.csv"
        if not db_path.exists():
            self.log.append_line("[ERROR] catalog.db not found in output folder.")
            return
        try:
            from pixai_gallery import export_csv
            export_csv(db_path, csv_path)
            self.log.append_line("Exported catalog to: {}".format(csv_path))
        except Exception as exc:
            self.log.append_line("[ERROR] " + str(exc))

    def _stop(self):
        if self._worker:
            self._worker.terminate()
            self._worker.wait(2000)
            self.log.append_line("\n[Stopped by user]")
            self._set_running(False)

    def _update_progress(self, done, total, _=0):
        if total > 0:
            self.prog_bar.setRange(0, total)
            self.prog_bar.setValue(done)
            self.prog_label.setText("{:,} / {:,} tasks".format(done, total))
        else:
            self.prog_bar.setRange(0, 0)

    def _on_done(self, success, msg):
        self._set_running(False)
        self.prog_bar.setRange(0, 1)
        self.prog_bar.setValue(1 if success else 0)
        self.prog_label.setText("Complete" if success else ("Error" if msg else "Stopped"))
        if not success and msg:
            self.log.append_line("\n[ERROR] " + msg)

    def _set_running(self, running):
        for b in (self.btn_probe, self.btn_count, self.btn_stats,
                  self.btn_backfill, self.btn_backfill_full, self.btn_export_csv,
                  self.btn_audit, self.btn_dedup, self.btn_verify,
                  self.btn_sync_artworks, self.btn_fix_models, self.btn_account):
            b.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        if running:
            self.prog_bar.setRange(0, 0)
            self.prog_label.setText("Working...")


# ---------------------------------------------------------------------------
# Gallery tab
# ---------------------------------------------------------------------------

class _GalleryServerThread(QThread):
    log   = Signal(str)
    ready = Signal(str)   # emits the base URL when server is ready

    def __init__(self, out_dir, port, rebuild_thumbs, host="127.0.0.1", parent=None):
        super().__init__(parent)
        self._out_dir = out_dir
        self._port = port
        self._rebuild_thumbs = rebuild_thumbs
        self._host = host
        self._server = None

    def run(self):
        if not _GALLERY_AVAILABLE:
            self.log.emit("[ERROR] Flask not installed — run: pip install flask")
            return
        try:
            out = Path(self._out_dir)
            app = gallery_mod.create_app(out)
            from pixai_gallery import load_catalog, build_thumbnails
            thumb_dir = out / "gallery" / "thumbs"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            rows = load_catalog(out / "catalog.db")
            missing = sum(1 for r in rows if r.get("filename") and
                          not (thumb_dir / f"{r['media_id']}.jpg").exists())
            if missing or self._rebuild_thumbs:
                label = "Rebuilding" if self._rebuild_thumbs else "Building"
                self.log.emit(f"{label} thumbnails ({missing if not self._rebuild_thumbs else len(rows)} images)…")
                _last_pct = [-1]
                def _thumb_progress(done, total, pct):
                    if pct - _last_pct[0] >= 5 or done == total:
                        self.log.emit(f"  Thumbnails: {done}/{total}  ({pct}%)")
                        _last_pct[0] = pct
                build_thumbnails(rows, out, thumb_dir,
                                 force=self._rebuild_thumbs,
                                 progress_cb=_thumb_progress)
                self.log.emit("Thumbnails done.")
            # Resolve display address for LAN mode
            if self._host == "0.0.0.0":
                import socket
                try:
                    display_ip = socket.gethostbyname(socket.gethostname())
                except Exception:
                    display_ip = "0.0.0.0"
            else:
                display_ip = self._host
            base_url = f"http://{display_ip}:{self._port}/"
            self.log.emit(f"Gallery server starting on {base_url}")
            if self._host == "0.0.0.0":
                self.log.emit(f"  (also accessible at http://127.0.0.1:{self._port}/ locally)")
            self.ready.emit(base_url)
            from werkzeug.serving import make_server
            self._server = make_server(self._host, self._port, app, threaded=True)
            self._server.serve_forever()
        except Exception as exc:
            self.log.emit(f"[ERROR] {exc}")

    def stop(self):
        if self._server:
            self._server.shutdown()


class GalleryTab(QWidget):

    def __init__(self, settings_bar, settings, parent=None):
        super().__init__(parent)
        self._bar = settings_bar
        self._server_thread = None

        if not _GALLERY_AVAILABLE:
            lay = QVBoxLayout(self)
            lbl = QLabel("Flask is not installed.  Run:  pip install flask")
            lbl.setStyleSheet("color: #f38ba8; font-size: 10pt; padding: 20px;")
            lay.addWidget(lbl)
            lay.addStretch()
            return

        # Controls
        ctrl = QGroupBox("Gallery Server")
        cg = QVBoxLayout(ctrl)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:"))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings.get("gallery_port", 5757))
        self.port.setFixedWidth(80)
        port_row.addWidget(self.port)
        port_row.addStretch()
        cg.addLayout(port_row)

        self.rebuild_thumbs = QCheckBox("Rebuild thumbnails on launch")
        self.rebuild_thumbs.setChecked(settings.get("gallery_rebuild_thumbs", False))
        cg.addWidget(self.rebuild_thumbs)

        self.lan_mode = QCheckBox("Allow access from other computers on local network  (bind 0.0.0.0)")
        self.lan_mode.setChecked(settings.get("gallery_lan", False))
        self.lan_mode.setToolTip(
            "When checked the gallery is reachable from any device on your LAN.\n"
            "Your local IP will be shown in the status bar after launch.\n"
            "Note: Windows Firewall may prompt you to allow access the first time.")
        cg.addWidget(self.lan_mode)

        btn_row = QHBoxLayout()
        self.btn_launch = QPushButton("▶  Launch Server")
        self.btn_launch.setObjectName("btn_start")
        self.btn_stop = QPushButton("■  Stop Server")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        self.btn_open = QPushButton("Open in Browser")
        self.btn_open.setEnabled(False)
        btn_row.addWidget(self.btn_launch)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_open)
        btn_row.addStretch()
        cg.addLayout(btn_row)

        self._status = QLabel("Server stopped")
        self._status.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 2px 0;")
        cg.addWidget(self._status)

        self.btn_launch.clicked.connect(self._launch)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_open.clicked.connect(self._open_browser)

        self.log = LogWidget()

        lay = QVBoxLayout(self)
        lay.addWidget(ctrl)
        lay.addWidget(self.log, stretch=1)

    def _launch(self):
        if self._server_thread and self._server_thread.isRunning():
            return
        port = self.port.value()
        host = "0.0.0.0" if self.lan_mode.isChecked() else "127.0.0.1"
        self.log.clear_log()
        self._server_thread = _GalleryServerThread(
            self._bar.out, port, self.rebuild_thumbs.isChecked(), host=host
        )
        self._server_thread.log.connect(self.log.append_line)
        self._server_thread.ready.connect(self._on_ready)
        self._server_thread.finished.connect(self._on_stopped)
        self._server_thread.start()
        self._status.setText(f"Starting on port {port}…")
        self.btn_launch.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_open.setEnabled(False)

    def _on_ready(self, base_url):
        self._base_url = base_url
        self._status.setText(f"Running — {base_url}")
        self._status.setStyleSheet("color: #a6e3a1; font-size: 9pt; padding: 2px 0;")
        self.btn_open.setEnabled(True)

    def _stop(self):
        if self._server_thread:
            self._server_thread.stop()
            self._server_thread.wait(3000)

    def _on_stopped(self):
        self._status.setText("Server stopped")
        self._status.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 2px 0;")
        self.btn_launch.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_open.setEnabled(False)

    def _open_browser(self):
        webbrowser.open(getattr(self, "_base_url", f"http://127.0.0.1:{self.port.value()}/"))

    def collect_settings(self):
        if not _GALLERY_AVAILABLE:
            return {}
        return {
            "gallery_port":           self.port.value(),
            "gallery_rebuild_thumbs": self.rebuild_thumbs.isChecked(),
            "gallery_lan":            self.lan_mode.isChecked(),
        }


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PixAI Gallery Backup v{}".format(core.__version__))
        self.setMinimumSize(860, 640)
        self.resize(960, 720)

        settings = _load_settings()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._sbar = SettingsBar(settings)
        root.addWidget(self._sbar)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)

        self._tabs = QTabWidget()
        self._dl_tab      = DownloadTab(self._sbar, settings)
        self._org_tab     = OrganizeTab(self._sbar, settings)
        self._conv_tab    = ConvertTab(self._sbar, settings)
        self._util_tab    = UtilitiesTab(self._sbar, settings)
        self._gallery_tab = GalleryTab(self._sbar, settings)
        self._tabs.addTab(self._dl_tab,      "  Download  ")
        self._tabs.addTab(self._org_tab,     "  Organize  ")
        self._tabs.addTab(self._conv_tab,    "  Convert   ")
        self._tabs.addTab(self._util_tab,    "  Utilities ")
        self._tabs.addTab(self._gallery_tab, "  Gallery   ")
        self._tabs.setCurrentIndex(settings.get("last_tab", 0))
        root.addWidget(self._tabs, stretch=1)

    def closeEvent(self, event):
        s = {
            "token":    self._sbar.token or "",
            "out":      self._sbar.out,
            "last_tab": self._tabs.currentIndex(),
        }
        s.update(self._dl_tab.collect_settings())
        s.update(self._org_tab.collect_settings())
        s.update(self._conv_tab.collect_settings())
        s.update(self._util_tab.collect_settings())
        s.update(self._gallery_tab.collect_settings())
        _save_settings(s)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
