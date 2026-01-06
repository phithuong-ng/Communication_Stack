import serial
import time
import struct
import sys

# --- CẤU HÌNH ---
PORT = 'COM2'  # <--- Giữ nguyên COM2 đang chạy ngon
BAUD = 115200

# --- ĐỊNH NGHĨA GIAO THỨC (PROTOCOL) ---
HEADER_BYTE = 0xAA
CMD_LED = 0x01  # Lệnh điều khiển LED
CMD_READ = 0x02  # Lệnh đọc FSR


class FSRController:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None

    def connect(self):
        """Mở cổng COM an toàn"""
        try:
            # Mẹo: Reset cổng trước khi mở để tránh Access Denied
            try:
                temp = serial.Serial(self.port, self.baud);
                temp.close()
            except:
                pass

            self.ser = serial.Serial(self.port, self.baud, timeout=1.0)
            print(f">> [SYSTEM] Da ket noi MCU tai {self.port}")
            time.sleep(2)  # Chờ MCU khởi động
            return True
        except Exception as e:
            print(f">> [ERROR] Khong the ket noi: {e}")
            return False

    def disconnect(self):
        """Đóng cổng COM"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print(">> [SYSTEM] Da ngat ket noi.")

    def send_frame(self, cmd_id, data=[]):
        """Hàm cốt lõi: Đóng gói và Gửi Frame điều khiển"""
        if not self.ser or not self.ser.is_open:
            print(">> [ERROR] Chua ket noi thiet bi!")
            return False

        # 1. Tính toán
        length = len(data)
        checksum = (HEADER_BYTE + cmd_id + length + sum(data)) % 256

        # 2. Đóng gói Binary (Struct)
        # B=unsigned char (1 byte)
        frame_format = 'BBB' + 'B' * length + 'B'
        packet = struct.pack(frame_format, HEADER_BYTE, cmd_id, length, *data, checksum)

        # 3. Gửi đi
        self.ser.write(packet)
        # print(f"[TX] Da gui lenh ID: {hex(cmd_id)}") # (Bật dòng này nếu muốn debug)
        return True

    def wait_response(self):
        """Lắng nghe phản hồi từ MCU"""
        if not self.ser: return None

        try:
            # Tìm Header 0xAA
            while self.ser.in_waiting > 0:
                if self.ser.read(1) == b'\xAA':
                    break
            else:
                return None  # Không có dữ liệu hoặc không thấy Header

            # Đọc 2 byte tiếp theo (Cmd, Len)
            head = self.ser.read(2)
            if len(head) < 2: return None
            cmd, length = struct.unpack('BB', head)

            # Đọc Data Payload
            payload = self.ser.read(length)

            # Đọc Checksum (để clear buffer)
            self.ser.read(1)

            return cmd, payload
        except Exception as e:
            print(f"Loi doc Serial: {e}")
            return None

    # --- CÁC HÀM CHỨC NĂNG CỤ THỂ (API) ---

    def command_read_fsr(self):
        """Gửi lệnh đọc và in kết quả"""
        self.send_frame(CMD_READ, [])  # Gửi lệnh 0x02, không data

        # Chờ phản hồi
        time.sleep(0.05)
        result = self.wait_response()

        if result:
            cmd, data = result
            if cmd == CMD_READ and len(data) == 8:
                volts, ohms = struct.unpack('ff', data)

                status = "THA LONG"
                if volts < 0.60: status = "DA AN"
                if volts < 0.40: status = "AN MANH"

                print(f"   => KET QUA: {volts:.4f}V | {ohms:.1f} Ohm ({status})")
            else:
                print("   => Loi: Du lieu tra ve sai cau truc.")
        else:
            print("   => Timeout: MCU khong tra loi.")

    def command_set_led(self, state):
        """Điều khiển LED (1=ON, 0=OFF)"""
        val = 1 if state else 0
        self.send_frame(CMD_LED, [val])  # Gửi lệnh 0x01, data=[1] hoặc [0]

        # Chờ xác nhận (ACK)
        time.sleep(0.05)
        result = self.wait_response()
        if result and result[0] == CMD_LED:
            print(f"   => MCU da xac nhan: LED {'ON' if state else 'OFF'}")
        else:
            print("   => MCU khong phan hoi.")


# --- CHƯƠNG TRÌNH CHÍNH (MENU) ---
if __name__ == "__main__":
    controller = FSRController(PORT, BAUD)

    if controller.connect():
        try:
            while True:
                print("\n--- BANG DIEU KHIEN ---")
                print(" [r] Doc cam bien FSR")
                print(" [1] Bat den LED (tren ESP32)")
                print(" [0] Tat den LED")
                print(" [q] Thoat")

                user_input = input("Nhap lenh cua ban: ").strip().lower()

                if user_input == 'r':
                    print("Dang gui lenh doc...")
                    controller.command_read_fsr()

                elif user_input == '1':
                    controller.command_set_led(True)

                elif user_input == '0':
                    controller.command_set_led(False)

                elif user_input == 'q':
                    print("Tam biet!")
                    break
                else:
                    print("Lenh khong hop le.")

        except KeyboardInterrupt:
            print("\nDung khan cap.")
        finally:
            controller.disconnect()