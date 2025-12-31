from PyQt5 import QtWidgets


class PasswordDialog(QtWidgets.QDialog):
    """Prompt for username/password when clocking in/out."""

    def __init__(self, username: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Staff Authentication")
        self.resize(360, 180)
        self._build_ui(username)

    def _build_ui(self, username: str) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.user_input = QtWidgets.QLineEdit(username)
        self.pass_input = QtWidgets.QLineEdit()
        self.pass_input.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("Username", self.user_input)
        form.addRow("Password", self.pass_input)
        layout.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QtWidgets.QPushButton("Submit")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_username(self) -> str:
        return self.user_input.text().strip()

    def get_password(self) -> str:
        return self.pass_input.text()
