from PyQt5 import QtCore, QtWidgets


class PaymentDialog(QtWidgets.QDialog):
    """Dialog to choose payment method, capture cash, and show change."""

    def __init__(self, total: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Payment")
        self.resize(460, 280)
        self.selected_method = "cash"
        self.tendered_amount: float | None = None
        self.total = total
        self._apply_style()
        self._build_ui()
        self._update_change()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QtWidgets.QLabel("Complete Payment")
        title.setStyleSheet("font-size:18px; font-weight:800;")
        layout.addWidget(title)

        total_label = QtWidgets.QLabel(f"Total due: ${self.total:.2f}")
        total_label.setStyleSheet("font-size:16px; font-weight:700; color:#0f172a;")
        layout.addWidget(total_label)

        method_box = QtWidgets.QGroupBox("Method")
        method_layout = QtWidgets.QHBoxLayout(method_box)
        self.cash_radio = QtWidgets.QRadioButton("Cash")
        self.card_radio = QtWidgets.QRadioButton("Card")
        self.cash_radio.setChecked(True)
        self.cash_radio.toggled.connect(self._toggle_cash_fields)
        method_layout.addWidget(self.cash_radio)
        method_layout.addWidget(self.card_radio)
        method_layout.addStretch()
        layout.addWidget(method_box)

        form = QtWidgets.QFormLayout()
        self.cash_input = QtWidgets.QDoubleSpinBox()
        self.cash_input.setMaximum(1_000_000)
        self.cash_input.setPrefix("$")
        self.cash_input.setDecimals(2)
        self.cash_input.setMinimumWidth(200)
        self.cash_input.valueChanged.connect(self._update_change)
        form.addRow("Cash received", self.cash_input)

        self.change_label = QtWidgets.QLabel("$0.00")
        self.change_label.setProperty("highlight", True)
        form.addRow("Change", self.change_label)
        layout.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.ok_btn = QtWidgets.QPushButton("Submit")
        self.ok_btn.setMinimumHeight(46)
        self.ok_btn.clicked.connect(self.accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setMinimumHeight(46)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

    def accept(self) -> None:  # type: ignore[override]
        self.selected_method = "cash" if self.cash_radio.isChecked() else "card"
        if self.selected_method == "cash":
            self.tendered_amount = float(self.cash_input.value())
            if self.tendered_amount < self.total:
                QtWidgets.QMessageBox.warning(self, "Insufficient", "Cash received is less than total.")
                return
        else:
            self.tendered_amount = None
        super().accept()

    def _toggle_cash_fields(self) -> None:
        is_cash = self.cash_radio.isChecked()
        self.cash_input.setEnabled(is_cash)
        self._update_change()

    def _update_change(self) -> None:
        if not self.cash_radio.isChecked():
            self.change_label.setText("$0.00")
            self.ok_btn.setEnabled(True)
            return
        tendered = float(self.cash_input.value())
        change = max(0.0, tendered - self.total)
        self.change_label.setText(f"${change:.2f}")
        self.ok_btn.setEnabled(tendered >= self.total)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background: #f7f9fc;
            }
            QGroupBox {
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                margin-top: 8px;
                padding: 8px;
                font-weight: 600;
            }
            QLabel {
                font-size: 13px;
                color: #0f172a;
            }
            QLabel[highlight="true"] {
                font-size: 18px;
                font-weight: 800;
                color: #10b981;
            }
            QRadioButton {
                font-size: 14px;
                padding: 6px;
            }
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 10px;
                padding: 12px 18px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
            QPushButton:disabled {
                background: #cbd5e1;
                color: #64748b;
            }
            QDoubleSpinBox {
                background: #ffffff;
                border: 1px solid #dfe3eb;
                border-radius: 10px;
                padding: 10px;
                font-size: 15px;
            }
            """
        )
