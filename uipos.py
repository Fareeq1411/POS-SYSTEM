"""
Simple PyQt5 POS UI inspired by the provided photo.
Run: source .venv/bin/activate && python uipos.py
"""

import contextlib
import json
import socket
import sys
from typing import Any
from PyQt5 import QtCore, QtGui, QtWidgets

from model import DatabaseError, ProductModel
from payment_dialog import PaymentDialog
from staff_dialog import PasswordDialog

LINKLY_HOST = "127.0.0.1"
LINKLY_PORT = 2005
LINKLY_TIMEOUT_SECS = 15


class LinklyError(Exception):
    """Raised when Linkly payment messaging fails."""


class POSWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Modern POS")
        self.resize(1400, 900)
        self.model: ProductModel | None = None
        self.row_products: dict[int, dict[str, Any]] = {}
        self.pad_mode: str | None = None
        self.pad_buffer: str = ""
        self.current_staff: dict[str, Any] | None = None
        self.current_attendance_id: int | None = None
        self._build_ui()
        self._apply_styles()
        self._wire_clock()
        self.model = self._init_model()
        self._warm_cache()
        self._start_cache_refresh_timer()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        main_layout.addWidget(self._build_left_panel(), 3)
        main_layout.addWidget(self._build_right_panel(), 1)
        main_layout.setStretch(0, 3)
        main_layout.setStretch(1, 1)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(12)

        layout.addLayout(self._build_header_bar())
        layout.addWidget(self._build_table())
        layout.addLayout(self._build_bottom_bar())
        return panel

    def _build_header_bar(self) -> QtWidgets.QLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)

        self.clock_label = QtWidgets.QLabel()
        self.clock_label.setAlignment(QtCore.Qt.AlignCenter)
        self.clock_label.setMinimumWidth(160)

        self.staff_combo = QtWidgets.QComboBox()
        self.staff_combo.setEditable(False)
        self.staff_combo.setMinimumWidth(200)
        self.staff_combo.addItem("Select staff", None)
        self.staff_combo.currentIndexChanged.connect(self._on_staff_changed)

        self.role_field = QtWidgets.QComboBox()
        self.role_field.addItems(["Cashier", "Stock Keeper", "Manager"])

        self.clock_button = QtWidgets.QPushButton("Clock In")
        self.clock_button.setMinimumHeight(36)
        self.clock_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.clock_button.clicked.connect(self.handle_clock_action)
        self.clock_button.setStyleSheet(
            "QPushButton {background:#2563eb; color:#fff; border:none; border-radius:8px; padding:8px 14px; font-weight:700;}"
            "QPushButton:hover {background:#1d4ed8;}"
        )

        self.customer_field = QtWidgets.QComboBox()
        self.customer_field.addItems(["Cash Customer", "Member", "Corporate", "Delivery"])

        bar.addWidget(self.clock_label, 1)
        bar.addWidget(self.staff_combo, 2)
        bar.addWidget(self.role_field, 1)
        bar.addWidget(self.clock_button, 1)
        bar.addWidget(self.customer_field, 2)
        return bar

    def _build_table(self) -> QtWidgets.QWidget:
        table = QtWidgets.QTableWidget(11, 4)
        table.setHorizontalHeaderLabels(["Description", "Qty", "Price", "Amount"])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setShowGrid(True)

        # Make the table height predictable.
        table.verticalHeader().setDefaultSectionSize(36)
        self.table = table
        return table

    def _build_bottom_bar(self) -> QtWidgets.QLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(10)

        bar.addWidget(self._build_action_panel(), 3)
        bar.addWidget(self._build_totals_card(), 2)
        bar.addWidget(self._build_keypad(), 2)
        return bar

    def _build_action_panel(self) -> QtWidgets.QWidget:
        buttons = [
            ("Weight", "#23c16b", 0, 0, 1, 1),
            ("Discount", "#23c16b", 0, 1, 1, 1),
            ("Payment", "#4d87ff", 0, 2, 2, 1),  # span two rows for easy tapping
            ("Void Item", "#7048e8", 1, 0, 1, 1),
            ("Void All Items", "#7048e8", 1, 1, 1, 1),
            ("Redeem Points", "#ff8a3d", 2, 0, 1, 1),
            ("Open Drawer", "#f2c94c", 2, 1, 1, 1),
            ("Print Receipt", "#f2c94c", 2, 2, 1, 1),
            ("Search Stock", "#3b82f6", 3, 0, 1, 1),
            ("Customer", "#3b82f6", 3, 1, 1, 1),
            ("Sales Refund", "#3b82f6", 3, 2, 1, 1),
            ("Recall Order", "#6b7280", 4, 0, 1, 1),
            ("Hold Order", "#6b7280", 4, 1, 1, 1),
            ("Change Operator", "#6b7280", 4, 2, 1, 1),
            ("Advanced", "#94a3b8", 5, 0, 1, 1),
            ("Exit", "#ef4444", 5, 1, 1, 1),
        ]

        container = QtWidgets.QFrame()
        container.setObjectName("Card")
        grid = QtWidgets.QGridLayout(container)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(8)

        for text, color, row, col, rowspan, colspan in buttons:
            height = 52
            if text == "Payment":
                height = 110
            btn = self._pill_button(text, color, height=height, bold=True)
            if text == "Search Stock":
                btn.clicked.connect(self.open_search_dialog)
            if text == "Void Item":
                btn.clicked.connect(self.void_selected_item)
            if text == "Void All Items":
                btn.clicked.connect(self.void_all_items)
            if text == "Payment":
                btn.clicked.connect(self.handle_payment)
            grid.addWidget(btn, row, col, rowspan, colspan)

        return container

    def _build_totals_card(self) -> QtWidgets.QWidget:
        container = QtWidgets.QFrame()
        container.setObjectName("Card")
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(16, 16, 16, 16)
        container_layout.setSpacing(10)

        title = QtWidgets.QLabel("Totals")
        title.setProperty("heading", True)
        container_layout.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

        self.amount_label = QtWidgets.QLabel("$0.00")
        self.discount_label = QtWidgets.QLabel("$0.00")
        self.redeem_label = QtWidgets.QLabel("$0.00")
        self.voucher_label = QtWidgets.QLabel("$0.00")
        for label, widget in [
            ("Amount", self.amount_label),
            ("Discount", self.discount_label),
            ("Redeem", self.redeem_label),
            ("Voucher", self.voucher_label),
        ]:
            widget.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            form.addRow(self._muted_label(label), widget)

        self.total_label = QtWidgets.QLabel("$0.00")
        self.total_label.setObjectName("TotalValue")
        self.total_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.addRow(self._muted_label("Total"), self.total_label)

        self.gst_label = QtWidgets.QLabel("$0.00")
        self.gst_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.addRow(self._muted_label("Total Incl. GST"), self.gst_label)

        self.items_label = QtWidgets.QLabel("0")
        self.items_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.addRow(self._muted_label("Total item(s)"), self.items_label)

        container_layout.addLayout(form)
        container_layout.addStretch()
        return container

    def _build_keypad(self) -> QtWidgets.QWidget:
        container = QtWidgets.QFrame()
        container.setObjectName("Card")
        layout = QtWidgets.QGridLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        # Display
        self.pad_display = QtWidgets.QLineEdit()
        self.pad_display.setReadOnly(True)
        self.pad_display.setPlaceholderText("Mode: —   Value: ")
        layout.addWidget(self.pad_display, 0, 0, 1, 4)

        # Controls row
        clear_btn = self._pill_button("Clear", "#ff9f43", height=42, bold=True)
        clear_btn.clicked.connect(self._pad_clear)
        layout.addWidget(clear_btn, 1, 0, 1, 2)

        plusminus_btn = self._pill_button("+/-", "#e5e7eb", height=42)
        plusminus_btn.clicked.connect(self._pad_toggle_sign)
        layout.addWidget(plusminus_btn, 1, 2)

        bag_btn = self._pill_button("Bag", "#22c55e", height=42, bold=True)
        layout.addWidget(bag_btn, 1, 3)

        # Number pad
        numbers = [
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
            ("4", 3, 0), ("5", 3, 1), ("6", 3, 2),
            ("7", 4, 0), ("8", 4, 1), ("9", 4, 2),
            ("0", 5, 0), (".", 5, 1),
        ]
        for text, row, col in numbers:
            btn = self._pill_button(text, "#f1f5f9", height=54, bold=True)
            btn.clicked.connect(lambda _, t=text: self._pad_append(t))
            layout.addWidget(btn, row, col)

        qty_btn = self._pill_button("Qty", "#2563eb", "#ffffff", height=54, bold=True)
        qty_btn.clicked.connect(lambda: self._pad_set_mode("qty"))
        layout.addWidget(qty_btn, 2, 3)

        price_btn = self._pill_button("Price", "#2563eb", "#ffffff", height=54, bold=True)
        price_btn.clicked.connect(lambda: self._pad_set_mode("price"))
        layout.addWidget(price_btn, 3, 3)

        enter_btn = self._pill_button("Enter", "#0ea5e9", "#ffffff", height=54, bold=True)
        enter_btn.clicked.connect(self._pad_apply)
        layout.addWidget(enter_btn, 4, 3, 2, 1)

        return container
        return container

    def _build_right_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setMaximumWidth(420)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(0)

        grid_widget = self._build_item_grid()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(grid_widget)
        layout.addWidget(scroll, 1)
        return panel

    def _build_item_grid(self) -> QtWidgets.QWidget:
        container = QtWidgets.QFrame()
        container.setObjectName("Card")
        grid = QtWidgets.QGridLayout(container)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(10)

        categories = self._categories()
        columns = 1
        for idx, (text, bg, fg) in enumerate(categories):
            row, col = divmod(idx, columns)
            btn = self._pill_button(text, bg, fg, height=72, bold=True)
            btn.setStyleSheet(
                f"QPushButton {{"
                f"background: {bg};"
                f"color: {fg};"
                "border: 1px solid #d9dde5;"
                "border-radius: 12px;"
                "padding: 14px;"
                "text-align: left;"
                "font-weight: 700;"
                "letter-spacing: 0.2px;"
                "}"
                "QPushButton:hover {border: 1px solid #cbd5e1;}"
            )
            grid.addWidget(btn, row, col)

        for col in range(columns):
            grid.setColumnStretch(col, 1)
        return container

    def _categories(self) -> list[tuple[str, str, str]]:
        """Shared category definitions for grid and bar."""
        return [
            ("Select category", "#e5e7eb", "#0f172a"),
            ("Fresh Produce", "#22c55e", "#ffffff"),
            ("Dairy & Eggs", "#f97316", "#ffffff"),
            ("Bakery", "#facc15", "#0f172a"),
            ("Meat & Seafood", "#ef4444", "#ffffff"),
            ("Pantry", "#8b5cf6", "#ffffff"),
            ("Beverages", "#0ea5e9", "#ffffff"),
            ("Snacks", "#f59e0b", "#0f172a"),
            ("Frozen", "#38bdf8", "#0f172a"),
            ("Household", "#10b981", "#ffffff"),
            ("Personal Care", "#6366f1", "#ffffff"),
            ("Health", "#14b8a6", "#ffffff"),
            ("Others", "#94a3b8", "#0f172a"),
        ]

    def _pill_button(
        self,
        text: str,
        bg: str,
        fg: str = "#0f172a",
        *,
        height: int = 48,
        bold: bool = False,
    ) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setMinimumHeight(height)
        weight = "600" if bold else "500"
        btn.setStyleSheet(
            f"QPushButton {{"
            f"background-color: {bg};"
            f"color: {fg};"
            f"border: none;"
            f"border-radius: 10px;"
            f"padding: 10px 14px;"
            f"font-weight: {weight};"
            f"}}"
            "QPushButton:hover {border: 1px solid #cbd5e1;}"
        )
        return btn

    def _muted_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("muted", True)
        return label

    def _wire_clock(self) -> None:
        self._update_clock()
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._clock_timer = timer

    def _update_clock(self) -> None:
        now = QtCore.QDateTime.currentDateTime()
        self.clock_label.setText(now.toString("dd/MM/yyyy  hh:mm:ss"))

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f4f5fb;
                color: #0f172a;
                font-family: "Helvetica Neue", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QMainWindow {
                background: #f4f5fb;
            }
            QFrame#Card {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 14px;
            }
            QLineEdit, QComboBox {
                background: #ffffff;
                border: 1px solid #dfe3eb;
                border-radius: 8px;
                padding: 8px 10px;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #dfe3eb;
                border-radius: 10px;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
            }
            QHeaderView::section {
                background: #f1f5f9;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                padding: 8px;
                font-weight: 600;
            }
            QLabel[muted="true"] {
                color: #6b7280;
            }
            QLabel[heading="true"] {
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#TotalValue {
                color: #ef4444;
                font-size: 22px;
                font-weight: 800;
            }
            """
        )

    # --- Data / model helpers -------------------------------------------------
    def _init_model(self) -> ProductModel | None:
        try:
            return ProductModel()
        except Exception as exc:
            self._show_error(f"Database not available: {exc}")
            return None

    def _warm_cache(self) -> None:
        """Load cached products or fetch all from DB on startup for faster lookups."""
        if not self.model:
            return
        try:
            self.model.prime_cache()
        except Exception as exc:
            self._show_info(f"Cache warm-up skipped: {exc}")
        self._sync_row_products_from_cache()
        self._load_staff_options()

    def _start_cache_refresh_timer(self) -> None:
        if not self.model:
            return
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self._refresh_cache)
        timer.start(30_000)  # 30 seconds
        self._cache_timer = timer

    def _refresh_cache(self) -> None:
        if not self.model:
            return
        try:
            self.model.refresh_cache()
        except Exception:
            return
        self._sync_row_products_from_cache()

    def _sync_row_products_from_cache(self) -> None:
        """Update in-table product metadata from latest cache."""
        if not self.model:
            return
        for row, product in list(self.row_products.items()):
            barcode = product.get("barcode")
            if not barcode:
                continue
            refreshed = self.model.get_cached_product(barcode)
            if refreshed:
                self.row_products[row] = refreshed

    def _load_staff_options(self) -> None:
        """Populate staff dropdown from staff DB."""
        if not self.model or not hasattr(self, "staff_combo"):
            return
        try:
            staff_list = self.model.list_active_staff()
        except Exception as exc:
            self._show_info(f"Could not load staff: {exc}")
            return
        self.staff_combo.clear()
        self.staff_combo.addItem("Select staff", None)
        for staff in staff_list:
            label = staff.get("name") or staff.get("username") or "Unknown"
            role = staff.get("role") or ""
            display = f"{label} ({role})" if role else label
            self.staff_combo.addItem(display, staff)

    def _on_staff_changed(self) -> None:
        """When staff selection changes, check for open attendance to set button label."""
        staff_data = self.staff_combo.currentData()
        if not staff_data or not self.model:
            self._update_clock_button_label(clockedin=False)
            self.current_staff = None
            self.current_attendance_id = None
            return
        try:
            today_att = self.model.get_today_attendance(staff_data["id"])
        except Exception:
            self._update_clock_button_label(clockedin=False)
            return
        self.current_staff = staff_data
        if today_att and not today_att.get("time_out"):
            self.current_attendance_id = today_att["id"]
            self._update_clock_button_label(clockedin=True)
        else:
            self.current_attendance_id = None
            self._update_clock_button_label(clockedin=False)

    def handle_scanned_barcode(self, barcode: str) -> None:
        """External hook: call this when scanner reads a barcode."""
        if not self.model:
            self._show_error("Database connection not ready.")
            return
        try:
            product = self.model.fetch_product_by_barcode(barcode)
        except DatabaseError as exc:
            self._show_error(f"Lookup failed: {exc}")
            return
        if not product:
            self._show_info(f"No product found for barcode {barcode}")
            return
        self._add_product_to_table(product)

    def open_search_dialog(self) -> None:
        if not self.model:
            self._show_error("Database connection not ready.")
            return
        dialog = SearchDialog(self.model, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted and dialog.selected_product:
            self._add_product_to_table(dialog.selected_product)

    def _add_product_to_table(self, product: dict[str, Any]) -> None:
        table: QtWidgets.QTableWidget = self.table
        qty = 1
        price = float(product.get("sell_price") or 0)
        if not self._can_use_qty(product, qty):
            return
        amount = price * qty

        row = self._find_empty_row(table)
        if row is None:
            row = table.rowCount()
            table.insertRow(row)

        table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(product.get("name", ""))))
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(qty)))
        table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{price:.2f}"))
        table.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{amount:.2f}"))
        table.selectRow(row)
        self.row_products[row] = product
        self._recalculate_totals()

    def _find_empty_row(self, table: QtWidgets.QTableWidget) -> int | None:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None or not item.text().strip():
                return row
        return None

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "Error", message)

    def _show_info(self, message: str) -> None:
        QtWidgets.QMessageBox.information(self, "Info", message)

    def void_selected_item(self) -> None:
        """Clear the currently selected row."""
        row = self.table.currentRow()
        if row < 0:
            self._show_info("Select a row to void.")
            return
        self._clear_row(row)
        self._recalculate_totals()

    def void_all_items(self) -> None:
        """Clear all rows."""
        for row in range(self.table.rowCount()):
            self._clear_row(row)
        self._recalculate_totals()

    def _clear_row(self, row: int) -> None:
        for col in range(self.table.columnCount()):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(""))
        self.row_products.pop(row, None)

    def _recalculate_totals(self) -> None:
        total = 0.0
        item_count = 0
        for row in range(self.table.rowCount()):
            desc_item = self.table.item(row, 0)
            if not desc_item or not desc_item.text().strip():
                continue
            qty_item = self.table.item(row, 1)
            price_item = self.table.item(row, 2)
            qty = self._safe_float(qty_item.text()) if qty_item else 0.0
            price = self._safe_float(price_item.text()) if price_item else 0.0
            line_total = qty * price
            total += line_total
            item_count += 1
            # keep amount column synced
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{line_total:.2f}"))

        self.amount_label.setText(f"${total:.2f}")
        self.total_label.setText(f"${total:.2f}")
        self.gst_label.setText(f"${total:.2f}")
        self.items_label.setText(str(item_count))

    def _safe_float(self, value: str) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    # --- Keypad state -------------------------------------------------------
    def _pad_set_mode(self, mode: str) -> None:
        self.pad_mode = mode
        self.pad_buffer = ""
        self._update_pad_display()

    def _pad_append(self, char: str) -> None:
        self.pad_buffer += char
        self._update_pad_display()

    def _pad_toggle_sign(self) -> None:
        if self.pad_buffer.startswith("-"):
            self.pad_buffer = self.pad_buffer[1:]
        elif self.pad_buffer:
            self.pad_buffer = "-" + self.pad_buffer
        self._update_pad_display()

    def _pad_clear(self) -> None:
        self.pad_buffer = ""
        self.pad_mode = None
        self._update_pad_display()

    def _pad_apply(self) -> None:
        if not self.pad_mode:
            self._show_info("Select Qty or Price before applying.")
            return
        if not self.pad_buffer:
            self._show_info("Enter a value first.")
            return
        row = self.table.currentRow()
        if row < 0:
            self._show_info("Select a row to update.")
            return
        value = self._safe_float(self.pad_buffer)
        if self.pad_mode == "qty":
            product = self.row_products.get(row, {})
            if not self._can_use_qty(product, value):
                self.pad_buffer = ""
                self._update_pad_display()
                return
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{value:g}"))
        elif self.pad_mode == "price":
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{value:.2f}"))
        self._recalculate_totals()
        self.pad_buffer = ""
        self._update_pad_display()

    def _update_pad_display(self) -> None:
        mode_label = self.pad_mode.capitalize() if self.pad_mode else "—"
        self.pad_display.setText(f"Mode: {mode_label}   Value: {self.pad_buffer}")

    # --- Payment flow -------------------------------------------------------
    def handle_payment(self) -> None:
        if self._cart_is_empty():
            self._show_info("Add items before payment.")
            return
        total = self._current_total()
        dialog = PaymentDialog(total, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        method = dialog.selected_method
        tendered = dialog.tendered_amount

        if method == "cash":
            if tendered is None or tendered < total:
                self._show_error("Cash received is insufficient.")
                return
            change = tendered - total
            self._open_cash_drawer()
            success = True
            error_message = None
        else:  # card
            success, error_message = self._process_card_payment(total)
            change = 0.0

        if not success:
            self._show_error(error_message or "Payment failed.")
            return

        items = self._collect_cart_items()
        saved = False
        with self._loading_overlay("Saving sale..."):
            try:
                saved = self.model.record_sale(items, method) if self.model else False
            except DatabaseError as exc:
                self._show_error(f"Could not store sale: {exc}")
                return

        if saved:
            self._show_info(f"Payment successful. Change: ${change:.2f}")
            self.void_all_items()
        else:
            self._show_error("Could not store sale.")

    def _cart_is_empty(self) -> bool:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text().strip():
                return False
        return True

    def _current_total(self) -> float:
        return self._safe_float(self.total_label.text().replace("$", ""))

    def _collect_cart_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in range(self.table.rowCount()):
            desc_item = self.table.item(row, 0)
            if not desc_item or not desc_item.text().strip():
                continue
            qty_item = self.table.item(row, 1)
            price_item = self.table.item(row, 2)
            qty = self._safe_float(qty_item.text()) if qty_item else 0.0
            price = self._safe_float(price_item.text()) if price_item else 0.0
            amount = qty * price
            product = self.row_products.get(row, {})
            items.append(
                {
                    "id": product.get("id"),
                    "name": product.get("name"),
                    "barcode": product.get("barcode"),
                    "qty": qty,
                    "price": price,
                    "amount": amount,
                    "deduct_unit": product.get("deduct_unit") or 1.0,
                }
            )
        return items

    def _open_cash_drawer(self) -> None:
        # Placeholder: integrate with actual drawer trigger.
        self._show_info("Cash drawer opened.")

    def _process_card_payment(self, total: float) -> tuple[bool, str | None]:
        items = self._collect_cart_items()
        payload = self._build_linkly_sale_payload(total, items)
        with self._loading_overlay("Sending to EFTPOS..."):
            try:
                response = self._send_linkly_payload(payload)
            except LinklyError as exc:
                return False, str(exc)
        approved, message = self._interpret_linkly_response(response)
        if not approved:
            return False, message or "Card payment declined."
        return True, None

    def _build_linkly_sale_payload(
        self,
        total: float,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        reference = QtCore.QDateTime.currentDateTime().toString("yyyyMMddHHmmss")
        staff_name = (self.current_staff or {}).get("username") or (self.current_staff or {}).get("name")
        payload_items = [
            {
                "name": item.get("name"),
                "barcode": item.get("barcode"),
                "qty": item.get("qty"),
                "price": item.get("price"),
                "amount": item.get("amount"),
            }
            for item in items
        ]
        return {
            "type": "SALE",
            "reference": reference,
            "amount_cents": int(round(total * 100)),
            "amount": round(total, 2),
            "currency": "MYR",
            "sub_total": self._money_label_value(self.amount_label),
            "discount_total": self._money_label_value(self.discount_label),
            "gst_total": self._money_label_value(self.gst_label),
            "items": payload_items,
            "operator": staff_name,
            "terminal": self.windowTitle(),
        }

    def _money_label_value(self, label: QtWidgets.QLabel) -> float:
        return self._safe_float(label.text().replace("$", "").strip())

    def _send_linkly_payload(self, payload: dict[str, Any]) -> bytes:
        message = self._encode_linkly_payload(payload)
        try:
            with socket.create_connection((LINKLY_HOST, LINKLY_PORT), timeout=LINKLY_TIMEOUT_SECS) as sock:
                sock.settimeout(LINKLY_TIMEOUT_SECS)
                sock.sendall(message)
                return self._read_linkly_response(sock)
        except (OSError, socket.timeout) as exc:
            raise LinklyError(f"Linkly connection failed: {exc}") from exc

    def _encode_linkly_payload(self, payload: dict[str, Any]) -> bytes:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return body.encode("utf-8") + b"\n"

    def _read_linkly_response(self, sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
        return b"".join(chunks).strip()

    def _interpret_linkly_response(self, response: bytes) -> tuple[bool, str | None]:
        if not response:
            return False, "No response from Linkly."
        text = response.decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            upper = text.upper()
            if "APPROVED" in upper or "SUCCESS" in upper:
                return True, None
            if "DECLINED" in upper or "FAILED" in upper or "ERROR" in upper:
                return False, text
            return False, f"Unrecognized Linkly response: {text}"
        approved = bool(parsed.get("approved") or parsed.get("success"))
        if approved:
            return True, None
        reason = parsed.get("message") or parsed.get("responseText") or "Card payment declined."
        return False, str(reason)

    @contextlib.contextmanager
    def _loading_overlay(self, text: str):
        dialog = QtWidgets.QProgressDialog(text, None, 0, 0, self)
        dialog.setWindowTitle("Please wait")
        dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(True)
        dialog.show()
        QtWidgets.QApplication.processEvents()
        try:
            yield
        finally:
            dialog.close()

    # --- Stock helpers ------------------------------------------------------
    def _can_use_qty(self, product: dict[str, Any], qty: float) -> bool:
        """Check stock before applying qty; allow override prompt."""
        if self._has_sufficient_stock(product, qty):
            return True
        name = product.get("name", "Item")
        stock = product.get("stock", 0)
        try:
            stock_val = float(stock)
            deduct_val = float(product.get("deduct_unit") or 1.0)
            deduct_val = deduct_val if deduct_val != 0 else 1.0
            available_units = stock_val / deduct_val
        except Exception:
            available_units = 0
        msg = (
            f"Not enough stock for {name}.\n"
            f"Requested qty: {qty}\n"
            f"Available qty: {available_units:.3f}"
        )
        override = QtWidgets.QMessageBox.question(
            self,
            "Insufficient stock",
            msg,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        return override == QtWidgets.QMessageBox.Yes

    def _has_sufficient_stock(self, product: dict[str, Any], qty: float) -> bool:
        if not product:
            return True
        stock = product.get("stock")
        deduct_unit = product.get("deduct_unit") or 1.0
        if stock is None:
            return True
        try:
            stock_val = float(stock)
            deduct_val = float(deduct_unit) if float(deduct_unit) != 0 else 1.0
            available_units = stock_val / deduct_val
        except Exception:
            return True
        return qty <= available_units + 1e-6

    # --- Attendance / staff -------------------------------------------------
    def handle_clock_action(self) -> None:
        if not self.model:
            self._show_error("Database connection not ready.")
            return
        staff_data = self.staff_combo.currentData()
        if not staff_data:
            self._show_info("Select staff.")
            return
        username = staff_data.get("username", "")
        pwd_dialog = PasswordDialog(username, self)
        if pwd_dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        username = pwd_dialog.get_username()
        password = pwd_dialog.get_password()
        if not password:
            self._show_info("Password required.")
            return

        try:
            staff = self.model.verify_staff_credentials(username, password)
        except DatabaseError as exc:
            self._show_error(f"Auth failed: {exc}")
            return
        if not staff:
            self._show_error("Invalid credentials or inactive staff.")
            return
        self.current_staff = staff

        try:
            today_att = self.model.get_today_attendance(staff["id"])
        except DatabaseError as exc:
            self._show_error(f"Attendance lookup failed: {exc}")
            return

        if today_att and not today_att.get("time_out"):
            # Clock out
            att_id = today_att["id"]
            try:
                success = self.model.clock_out(att_id)
            except DatabaseError as exc:
                self._show_error(f"Clock-out failed: {exc}")
                return
            if success:
                self._show_info(f"{staff.get('name') or staff.get('username')} clocked out.")
                self.current_attendance_id = None
                self._update_clock_button_label(clockedin=False)
            else:
                self._show_error("Clock-out did not update.")
        else:
            # Clock in
            role = self.role_field.currentText()
            salary = float(staff.get("salary") or 0.0)
            try:
                att_id = self.model.clock_in(staff["id"], role, salary=salary)
            except DatabaseError as exc:
                self._show_error(f"Clock-in failed: {exc}")
                return
            self.current_attendance_id = att_id
            self._show_info(f"{staff.get('name') or staff.get('username')} clocked in as {role}.")
            self._update_clock_button_label(clockedin=True)

    def _update_clock_button_label(self, clockedin: bool) -> None:
        self.clock_button.setText("Clock Out" if clockedin else "Clock In")


class SearchDialog(QtWidgets.QDialog):
    """Simple dialog to search products by name/barcode and pick one."""

    def __init__(self, model: ProductModel, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.selected_product: dict[str, Any] | None = None
        self.setWindowTitle("Search Stock")
        self.resize(420, 420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Enter name or barcode...")
        self.input.returnPressed.connect(self.perform_search)
        layout.addWidget(self.input)

        self.results = QtWidgets.QListWidget()
        self.results.itemDoubleClicked.connect(self._accept_current)
        layout.addWidget(self.results, 1)

        btn_row = QtWidgets.QHBoxLayout()
        search_btn = QtWidgets.QPushButton("Search")
        search_btn.clicked.connect(self.perform_search)
        ok_btn = QtWidgets.QPushButton("Add")
        ok_btn.clicked.connect(self._accept_current)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(search_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def perform_search(self) -> None:
        query = self.input.text().strip()
        if not query:
            return
        try:
            results = self.model.search_products(query, limit=12)
        except DatabaseError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", f"Search failed: {exc}")
            return

        self.results.clear()
        if not results:
            self.results.addItem("(No results)")
            self.results.setEnabled(False)
            return

        self.results.setEnabled(True)
        for product in results:
            name = product.get("name", "Unknown")
            barcode = product.get("barcode", "")
            price = product.get("sell_price") or 0
            item = QtWidgets.QListWidgetItem(f"{name}  [{barcode}]  RM {price:.2f}")
            item.setData(QtCore.Qt.UserRole, product)
            self.results.addItem(item)
        self.results.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.results.currentItem()
        if not item:
            return
        product = item.data(QtCore.Qt.UserRole)
        if not product or not isinstance(product, dict):
            return
        self.selected_product = product
        self.accept()


def main() -> None:
    if sys.platform.startswith("win"):
        QtCore.QLoggingCategory.setFilterRules("qt.qpa.fonts=false")
    app = QtWidgets.QApplication(sys.argv)
    if sys.platform.startswith("win"):
        app.setFont(QtGui.QFont("Segoe UI", 10))
    app.setApplicationName("Modern POS")
    window = POSWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
