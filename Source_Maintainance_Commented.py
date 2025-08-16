# Ion Source Maintenance Control UI
# - PyQt5 GUI to guide maintenance, show live states, and send manual commands
# - Talks to hardware via OPC UA (python-opcua)
# - QTimer polls states and updates indicators; buttons write Boolean nodes

import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QMessageBox, QDialog, QFormLayout
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPalette
from opcua import Client, ua


# --- UI WIDGETS --------------------------------------------------------------

class StateIndicator(QLabel):
    # Small square that shows a Boolean state (green=True, red=False).
    # Used in the main window's "Source States" group, updated by update_states().
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)              # constant size for all indicators
        self.set_state(False)                  # default to False/red

    def set_state(self, state: bool):
        # Set background color based on state; called from update_states() and set_all_indicators().
        palette = self.palette()
        color = QColor(0, 255, 0) if state else QColor(255, 0, 0)
        palette.setColor(QPalette.Window, color)
        self.setAutoFillBackground(True)
        self.setPalette(palette)
        self.update()


class MaintenanceGuide(QDialog):
    # Modal step-by-step guide. Shown by show_open_guide()/show_close_guide().
    def __init__(self, steps, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)
        self.step_label = QLabel()
        self.step_label.setWordWrap(True)
        layout.addWidget(self.step_label)

        self.current_step = 0
        self.steps = steps

        self.next_button = QPushButton("Next Step")
        self.next_button.clicked.connect(self.next_step)
        layout.addWidget(self.next_button)

        self.update_step()                     # show first step immediately

    def update_step(self):
        # Render current step and switch button text on last step.
        if self.current_step < len(self.steps):
            step_text = self.steps[self.current_step]
            self.step_label.setText(f"Step {self.current_step + 1}: {step_text}")
            if self.current_step == len(self.steps) - 1:
                self.next_button.setText("Finish")

    def next_step(self):
        # Advance; close dialog after final step.
        self.current_step += 1
        if self.current_step >= len(self.steps):
            self.accept()
        else:
            self.update_step()


# --- MAIN WINDOW -------------------------------------------------------------

class IonizerMaintenanceControl(QMainWindow):
    # Top-level window: builds UI, manages OPC UA client, polls states, and sends commands.
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ion Source Maintenance Control")
        self.setGeometry(100, 100, 800, 600)

        # Logical names -> OPC UA NodeIds; used by update_states() and set_node_state()
        self.nodes = {
            'wheel': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Wheel",
            'source_valve': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Valve_Source",
            'vent': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Vent",
            'pump_valve': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Valve_Pump",
            'pump': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Pump"
        }

        self.client = None                     # OPC UA client instance (lazy connect)
        self.server_url = "opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2"

        self.init_ui()                         # build all UI sections

        # Poll states every second; timer drives update_states()
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_states)
        self.monitor_timer.start(1000)

    def init_ui(self):
        # Build: maintenance guides, live state indicators, and manual controls.
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # --- Maintenance Guides (launch step-by-step dialogs) ---
        guide_group = QGroupBox("Maintenance Guides")
        guide_layout = QHBoxLayout()
        self.open_guide_btn = QPushButton("Open Source Guide")
        self.open_guide_btn.clicked.connect(self.show_open_guide)
        self.close_guide_btn = QPushButton("Close Source Guide")
        self.close_guide_btn.clicked.connect(self.show_close_guide)
        guide_layout.addWidget(self.open_guide_btn)
        guide_layout.addWidget(self.close_guide_btn)
        guide_group.setLayout(guide_layout)
        main_layout.addWidget(guide_group)

        # --- Live State Indicators (driven by update_states()) ---
        state_group = QGroupBox("Source States")
        state_layout = QFormLayout()
        self.wheel_indicator = StateIndicator()
        state_layout.addRow("Sample Wheel Retracted:", self.wheel_indicator)
        self.source_valve_indicator = StateIndicator()
        state_layout.addRow("Source Valve Closed:", self.source_valve_indicator)
        self.vent_indicator = StateIndicator()
        state_layout.addRow("Venting Active:", self.vent_indicator)
        self.pump_valve_indicator = StateIndicator()
        state_layout.addRow("Pump Valve Open:", self.pump_valve_indicator)
        self.pump_indicator = StateIndicator()
        state_layout.addRow("Pumping Active:", self.pump_indicator)
        state_group.setLayout(state_layout)
        main_layout.addWidget(state_group)

        # --- Manual Controls (call set_node_state() / confirm_* wrappers) ---
        control_group = QGroupBox("Manual Controls")
        control_layout = QVBoxLayout()

        # Sample Wheel
        wheel_group = QGroupBox("Sample Wheel")
        wheel_layout = QHBoxLayout()
        self.retract_wheel_btn = QPushButton("Retract Sample Wheel")
        self.retract_wheel_btn.clicked.connect(lambda: self.set_node_state('wheel', True))
        self.drive_in_wheel_btn = QPushButton("Drive In Sample Wheel")
        self.drive_in_wheel_btn.clicked.connect(lambda: self.set_node_state('wheel', False))
        wheel_layout.addWidget(self.retract_wheel_btn)
        wheel_layout.addWidget(self.drive_in_wheel_btn)
        wheel_group.setLayout(wheel_layout)
        control_layout.addWidget(wheel_group)

        # Source Valve
        valve_group = QGroupBox("Source Valve")
        valve_layout = QHBoxLayout()
        self.close_source_valve_btn = QPushButton("Close Source Valve")
        self.close_source_valve_btn.clicked.connect(lambda: self.set_node_state('source_valve', True))
        self.open_source_valve_btn = QPushButton("Open Source Valve")
        self.open_source_valve_btn.clicked.connect(lambda: self.set_node_state('source_valve', False))
        valve_layout.addWidget(self.close_source_valve_btn)
        valve_layout.addWidget(self.open_source_valve_btn)
        valve_group.setLayout(valve_layout)
        control_layout.addWidget(valve_group)

        # Argon Venting
        vent_group = QGroupBox("Argon Venting")
        vent_layout = QHBoxLayout()
        self.start_vent_btn = QPushButton("Start Argon Venting")
        self.start_vent_btn.clicked.connect(self.confirm_start_venting)  # shows confirmation, then set_node_state('vent', True)
        self.stop_vent_btn = QPushButton("Stop Argon Venting")
        self.stop_vent_btn.clicked.connect(lambda: self.set_node_state('vent', False))
        vent_layout.addWidget(self.start_vent_btn)
        vent_layout.addWidget(self.stop_vent_btn)
        vent_group.setLayout(vent_layout)
        control_layout.addWidget(vent_group)

        # Pump Valve
        pump_valve_group = QGroupBox("Pump Valve")
        pump_valve_layout = QHBoxLayout()
        self.open_pump_valve_btn = QPushButton("Open Pump Valve")
        self.open_pump_valve_btn.clicked.connect(self.confirm_open_pump_valve)  # confirm, then set_node_state('pump_valve', True)
        self.close_pump_valve_btn = QPushButton("Close Pump Valve")
        self.close_pump_valve_btn.clicked.connect(lambda: self.set_node_state('pump_valve', False))
        pump_valve_layout.addWidget(self.open_pump_valve_btn)
        pump_valve_layout.addWidget(self.close_pump_valve_btn)
        pump_valve_group.setLayout(pump_valve_layout)
        control_layout.addWidget(pump_valve_group)

        # Pump
        pump_group = QGroupBox("Pump")
        pump_layout = QHBoxLayout()
        self.start_pump_btn = QPushButton("Start Pumping")
        self.start_pump_btn.clicked.connect(lambda: self.set_node_state('pump', True))
        self.stop_pump_btn = QPushButton("Stop Pumping")
        self.stop_pump_btn.clicked.connect(lambda: self.set_node_state('pump', False))
        pump_layout.addWidget(self.start_pump_btn)
        pump_layout.addWidget(self.stop_pump_btn)
        pump_group.setLayout(pump_layout)
        control_layout.addWidget(pump_group)

        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)

    # --- Guide launchers (construct and run MaintenanceGuide dialogs) ---

    def show_open_guide(self):
        steps = [
            "Press Sample Wheel Retract",
            # ... additional detailed instructions
            "Done!"
        ]
        guide = MaintenanceGuide(steps, "Opening Source Guide", self)
        guide.exec_()                           # blocks until finished/cancelled

    def show_close_guide(self):
        steps = [
            "Put the Wheel back in",
            # ... additional detailed instructions
            "Done!"
        ]
        guide = MaintenanceGuide(steps, "Closing Source Guide", self)
        guide.exec_()

    # --- OPC UA connectivity and polling -------------------------------------

    def connect_opc(self) -> bool:
        # (Re)connect to OPC UA server; disconnect any existing client first.
        try:
            if self.client:
                try:
                    self.client.disconnect()
                except:
                    pass
            self.client = Client(self.server_url)
            self.client.connect()
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            self.client = None
            return False

    def update_states(self):
        # Timer callback: ensure connection, read all Boolean nodes, update indicators.
        # On failure: drop connection and set all indicators to False/red.
        if not self.client:
            if not self.connect_opc():
                self.set_all_indicators(False)
                return
        try:
            values = {}
            for name, node_id in self.nodes.items():
                values[name] = self.client.get_node(node_id).get_value()
            self.wheel_indicator.set_state(values['wheel'])
            self.source_valve_indicator.set_state(values['source_valve'])
            self.vent_indicator.set_state(values['vent'])
            self.pump_valve_indicator.set_state(values['pump_valve'])
            self.pump_indicator.set_state(values['pump'])
        except Exception as e:
            print(f"Error updating states: {e}")
            try:
                if self.client:
                    self.client.disconnect()
            except:
                pass
            self.client = None
            self.set_all_indicators(False)

    def set_all_indicators(self, state: bool):
        # Helper to force all indicators to a single state (e.g., fault/disconnected).
        for indicator in [
            self.wheel_indicator,
            self.source_valve_indicator,
            self.vent_indicator,
            self.pump_valve_indicator,
            self.pump_indicator
        ]:
            indicator.set_state(state)

    # --- Command writes -------------------------------------------------------

    def set_node_state(self, node_name: str, state: bool) -> bool:
        # Write a Boolean to the specified OPC UA node. Used by control buttons and confirmations.
        if not self.client:
            if not self.connect_opc():
                QMessageBox.critical(self, "Error", "Could not connect to OPC server")
                return False
        try:
            node = self.client.get_node(self.nodes[node_name])
            node.set_value(ua.Variant(state, ua.VariantType.Boolean))
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not set {node_name} state: {e}")
            try:
                if self.client:
                    self.client.disconnect()
            except:
                pass
            self.client = None
            return False

    # --- Safety confirmations -------------------------------------------------

    def confirm_start_venting(self):
        # Confirm before enabling venting, then write to 'vent' if approved.
        reply = QMessageBox.question(
            self, 'Confirm Venting',
            "Are you sure you want to start argon venting?\n\nThis will release gas into the system.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.set_node_state('vent', True)

    def confirm_open_pump_valve(self):
        # Confirm before opening pump valve, then write to 'pump_valve' if approved.
        reply = QMessageBox.question(
            self, 'Confirm Pump Valve',
            "Are you sure you want to open the pump valve?\n\nThis will connect the pump to the system.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.set_node_state('pump_valve', True)

    # --- Shutdown handling ----------------------------------------------------

    def closeEvent(self, event):
        # Ensure clean OPC UA disconnect on window close.
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
        event.accept()


# --- ENTRY POINT -------------------------------------------------------------

if __name__ == "__main__":
    # Create the Qt app, show main window, and run the event loop.
    app = QApplication(sys.argv)
    window = IonizerMaintenanceControl()
    window.show()
    sys.exit(app.exec_())
