"""Dialogs: choosing a Jimaku entry, and Settings."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressDialog,
    QPushButton, QVBoxLayout,
)

from ..config import config_path
from ..dictionary import ATTRIBUTION
from ..dictionary.build import install, installed_info
from ..dictionary.lookup import reset_shared


class DictInstallThread(QThread):
    progress = Signal(str, float)
    done = Signal(str)  # "" on success, else the error

    def run(self) -> None:
        try:
            install(lambda m, f: self.progress.emit(m, f))
            self.done.emit("")
        except Exception as e:  # network, disk, unexpected format...
            self.done.emit(str(e))
from ..identify import MediaInfo
from ..jimaku import Entry


class EntryChoiceDialog(QDialog):
    """"Several Jimaku entries match - which one is this video?" """

    def __init__(self, info: MediaInfo, candidates: list[Entry], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Which show is this?")
        self.setMinimumWidth(620)
        self._candidates = candidates

        what = info.title
        if info.season:
            what += f" season {info.season}"
        if info.episode is not None:
            what += f", episode {info.episode}"
        intro = QLabel(
            f"<b>{info.path.name}</b><br>"
            f"Several Jimaku entries match <i>{what}</i>. Pick the right one. "
            "Your choice is remembered for the other episodes in this folder.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)

        self.list = QListWidget()
        for e in candidates:
            item = QListWidgetItem(e.label())
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(self.accept)

        buttons = QDialogButtonBox()
        buttons.addButton("Use this one", QDialogButtonBox.AcceptRole)
        buttons.addButton("Skip this video", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.list)
        lay.addWidget(buttons)

    def chosen(self) -> Entry | None:
        row = self.list.currentRow()
        return self._candidates[row] if 0 <= row < len(self._candidates) else None


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hatsumimi settings")
        self.setMinimumWidth(520)
        self._cfg = dict(cfg)

        self.key = QLineEdit(cfg.get("jimaku_api_key", ""))
        self.key.setEchoMode(QLineEdit.Password)
        from ..config import find_api_key
        self.key.setPlaceholderText(
            "using the key from API.txt / JIMAKU_API_KEY (paste one here to save it in settings)"
            if find_api_key() and not cfg.get("jimaku_api_key") else "paste your Jimaku API key")
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key)
        key_row.addWidget(show)

        help_ = QLabel('Get a free key from your account page on '
                       '<a href="https://jimaku.cc">jimaku.cc</a>.')
        help_.setOpenExternalLinks(True)

        self.sync = QCheckBox("Re-time subtitles to match each video")
        self.sync.setChecked(bool(cfg.get("sync", True)))
        self.mux = QCheckBox("Embed subtitles into .mkv files (instead of a separate file)")
        self.mux.setChecked(bool(cfg.get("mux", True)))

        where = QLabel(f"Saved in {config_path()}")
        where.setStyleSheet("color: gray;")
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # Dictionary for hover lookups
        self.dict_status = QLabel()
        self.dict_btn = QPushButton()
        self.dict_btn.clicked.connect(self._install_dictionary)
        dict_row = QHBoxLayout()
        dict_row.addWidget(self.dict_status, 1)
        dict_row.addWidget(self.dict_btn)
        credit = QLabel(ATTRIBUTION)
        credit.setWordWrap(True)
        credit.setStyleSheet("color: gray; font-size: 11px;")
        self._refresh_dict_status()

        form = QFormLayout()
        form.addRow("Jimaku API key", key_row)
        form.addRow("", help_)
        form.addRow("", self.sync)
        form.addRow("", self.mux)
        form.addRow("Dictionary", dict_row)
        form.addRow("", credit)

        # External tools
        from ..tools import check_tools, install_hint
        tools = check_tools()
        lines = [f"{'✔' if t.path else '✖'} {t.name}" + ("" if t.path else f" (needed for {t.needed_for})")
                 for t in tools]
        tools_label = QLabel("    ".join(lines))
        if not all(t.path for t in tools):
            tools_label.setText(tools_label.text() + f"<br>Install with: <code>{install_hint()}</code>")
            tools_label.setTextFormat(Qt.RichText)
        tools_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tools_label.setWordWrap(True)
        form.addRow("Tools", tools_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(where)
        lay.addWidget(buttons)

    def _refresh_dict_status(self) -> None:
        info = installed_info()
        if info:
            self.dict_status.setText(f"Installed: {int(info['words']):,} words, {int(info['kanji']):,} kanji "
                                     f"(JMdict {info['version'].split('+')[-1][:8]})")
            self.dict_btn.setText("Update dictionary")
        else:
            self.dict_status.setText("Not installed (needed for hover lookups, about 13 MB download)")
            self.dict_btn.setText("Download dictionary")

    def _install_dictionary(self) -> None:
        dlg = QProgressDialog("Starting…", None, 0, 100, self)
        dlg.setWindowTitle("Dictionary")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumWidth(420)
        dlg.setMinimumDuration(0)
        worker = DictInstallThread(self)

        def on_progress(msg: str, frac: float):
            dlg.setLabelText(msg)
            if frac < 0:
                dlg.setRange(0, 0)  # busy indicator
            else:
                dlg.setRange(0, 100)
                dlg.setValue(int(frac * 100))

        def on_done(error: str):
            dlg.close()
            reset_shared()
            self._refresh_dict_status()
            if error:
                QMessageBox.warning(self, "Dictionary", f"Couldn't install the dictionary:\n{error}")

        worker.progress.connect(on_progress)
        worker.done.connect(on_done)
        reset_shared()  # close the open dictionary: Windows can't replace a file that's in use
        self.dict_btn.setEnabled(False)
        worker.finished.connect(lambda: self.dict_btn.setEnabled(True))
        worker.start()
        dlg.exec()

    def result_config(self) -> dict:
        cfg = dict(self._cfg)
        cfg["jimaku_api_key"] = self.key.text().strip()
        cfg["sync"] = self.sync.isChecked()
        cfg["mux"] = self.mux.isChecked()
        return cfg
