import os
import sqlite3
import random
import time
import threading
import json
import csv
import re
from io import StringIO
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from imap_tools import MailBox, A

from flask import Flask, render_template, request, flash, redirect, url_for, session, jsonify, Response
from dotenv import load_dotenv
import requests
import serial
import paho.mqtt.client as mqtt

# Safely import Raspberry Pi GPIO (Prevents crashing if tested on a Windows/Mac laptop)
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key")

# --- CONFIGURATION & STATE ---
# Individual thresholds for each fridge (Default 25.0°C)
fridge_thresholds = {"Frig1": 25.0, "Frig2": 25.0, "Frig_BT": 25.0}     

FAN_IS_ON = False          
alert_sent = {"Frig1": False, "Frig2": False, "Frig_BT": False, "Email_Override": False}
# --- OPTION 1: TEST LED (Currently Active) ---
LED_PIN = 24 

def setup_fan():
    if not GPIO: return
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(LED_PIN, GPIO.OUT)
        GPIO.output(LED_PIN, GPIO.LOW) 
    except Exception as e:
        print(f"GPIO Setup Error: {e}")

setup_fan()

def evaluate_cooling_system(fridge_name, current_temp):
    """Phase 2 Logic: Requests permission if hot, resets if safe."""
    global FAN_IS_ON
    
    # Check this specific fridge's threshold!
    limit = fridge_thresholds.get(fridge_name, 25.0)
    
    if current_temp > limit:
        # If it's hot, the fan is OFF, and we HAVEN'T asked for permission yet...
        if not alert_sent.get(fridge_name, False) and not FAN_IS_ON:
            print(f"🚨 {fridge_name} is hot ({current_temp}°C - Limit {limit}°C)! Requesting permission...", flush=True)
            alert_sent[fridge_name] = True
            threading.Thread(target=send_approval_email, args=(fridge_name, current_temp), daemon=True).start()
    else:
        # If it's safe (or you manually lowered the threshold), turn everything OFF and reset
        if FAN_IS_ON or alert_sent.get(fridge_name, False):
            print(f"❄️ {fridge_name} is safe ({current_temp}°C - Limit {limit}°C). Resetting system.", flush=True)
            FAN_IS_ON = False
            alert_sent[fridge_name] = False
            
            if GPIO: GPIO.output(LED_PIN, GPIO.LOW)
            sensor_state['fan_status'] = "OFF"
            mqtt_client.publish("FanStatus", "OFF")

# ==========================================
# PHASE 2 EMAIL LOGIC
# ==========================================

def send_approval_email(fridge_name, temp):
    """Sends the Phase 2 approval request email."""
    try:
        sender_email = os.getenv("SENDER_EMAIL") 
        sender_password = os.getenv("EMAIL_PASS") 
        admin_email = "iotmajeed2026@gmail.com" 

        # Create a proper MIME message to handle UTF-8 characters like '°'
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = admin_email
        message["Subject"] = f"ALERT: {fridge_name} Temperature High"

        body = f"The current temperature is {temp}°C. Turn on fan? Reply YES."
        
        # The "utf-8" argument here is what fixes the crash!
        message.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        
        # Send the MIME message instead of raw text
        server.send_message(message) 
        server.quit()
        
        # Publish Phase 2 System Notification to Dashboard
        mqtt_client.publish("SystemNotification", f"ALERT: {fridge_name} is {temp}°C! Awaiting Email Approval.")
        print(f"🚨 Permission Email Sent to {admin_email}!", flush=True)
        
    except Exception as e:
        print(f"DEBUG EMAIL ERROR: {e}")

def listen_for_email_commands():
    """Phase 2 Listener - Reverted to strictly check UNREAD emails only."""
    global FAN_IS_ON 
    
    username = os.getenv("SENDER_EMAIL")
    password = os.getenv("EMAIL_PASS")
    AUTHORIZED_ADMIN = "iotmajeed2026@gmail.com" 
    
    print("📧 Starting Phase 2 IMAP-Tools Listener (Unread Only Mode)...", flush=True)
    
    while True:
        try:
            # The system only checks the inbox if an alert is active and the fan is still OFF
            if any(alert_sent.values()) and not FAN_IS_ON:
                
                with MailBox("imap.gmail.com").login(username, password, "INBOX") as mb:
                    # STRICT FIX: Process only UNSEEN (unread) emails 
                    # This ensures it won't trigger from an old "YES" reply
                    for msg in mb.fetch(A(seen=False)):
                        
                        # Security Check: Must be from your authorized admin email 
                        if msg.from_.lower() == AUTHORIZED_ADMIN.lower():
                            
                            # Robust Check: Subject + Body for the "YES" command 
                            full_content = (msg.subject + " " + msg.text).upper()
                            
                            if "YES" in full_content:
                                print(f"✅ NEW Authorized 'YES' received from {AUTHORIZED_ADMIN}! Activating Fan...", flush=True)
                                
                                FAN_IS_ON = True
                                
                                if GPIO: 
                                    GPIO.output(LED_PIN, GPIO.HIGH) 
                                
                                sensor_state['fan_status'] = "ON"
                                mqtt_client.publish("FanStatus", "ON")
                                mqtt_client.publish("SystemNotification", "Fan activated via Email Approval.")
                                break 
                        else:
                            print(f"🔒 SECURITY BLOCK: Ignored unseen email from unauthorized sender: {msg.from_}", flush=True)
                            
        except Exception as e:
            print(f"💥 [IMAP ERROR]: {e}", flush=True)
        
        # Check every 10 seconds
        time.sleep(10)

        
# --- OPTION 2: L293D MOTOR DRIVER (Commented Out) ---
# To use the real fan, comment out Option 1 above, and uncomment this block:

# FAN_ENA = 22 
# FAN_IN1 = 27
# FAN_IN2 = 17

# def setup_fan():
#     if not GPIO: return
#     try:
#         GPIO.setmode(GPIO.BCM)
#         GPIO.setwarnings(False)
#         GPIO.setup(FAN_ENA, GPIO.OUT)
#         GPIO.setup(FAN_IN1, GPIO.OUT)
#         GPIO.setup(FAN_IN2, GPIO.OUT)
#         GPIO.output(FAN_ENA, GPIO.LOW)
#     except Exception as e:
#         print(f"GPIO Setup Error: {e}")

# def spin_fan_task(duration=5):
#     if not GPIO: return
#     print(f"❄️ Activating Cooling Fan for {duration} seconds...", flush=True)
#     GPIO.output(FAN_IN1, GPIO.LOW)
#     GPIO.output(FAN_IN2, GPIO.HIGH)
#     GPIO.output(FAN_ENA, GPIO.HIGH)
#     time.sleep(duration)
#     GPIO.output(FAN_ENA, GPIO.LOW)
#     print("🛑 Cooling Fan stopped.", flush=True)



# ==========================================
# DATABASE & HARDWARE LOGIC
# ==========================================

def get_db_connection():
    conn = sqlite3.connect('store.db', timeout=10.0) # Timeout prevents database lock errors
    conn.row_factory = sqlite3.Row 
    return conn

def read_bulk_rfid():
    try:
        ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.2)
        ser.reset_input_buffer() 
        
        start_time = time.time()
        last_poll = 0
        raw_hex_stream = ""
        
        while time.time() - start_time < 2.0:
            if time.time() - last_poll > 0.1:
                ser.write(bytes.fromhex("0008220000000022"))
                last_poll = time.time()
                
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                raw_hex_stream += data.hex().upper()
                
        ser.close()
        return raw_hex_stream 
    except Exception as e:
        print(f"RFID Hardware Error: {e}")
        return ""
    
def normalize_epc(raw_epc):
    if not raw_epc: return ""
    clean_id = raw_epc[:-2] 
    if clean_id.startswith("0000CF"):
        unique_part = clean_id[-4:] 
    else:
        unique_part = clean_id[-4:]
    return unique_part.zfill(22)

sensor_state = {
    "Frig1": {"temperature": 0, "humidity": 0},
    "Frig2": {"temperature": 0, "humidity": 0},
    "Frig_BT": {"temperature": 0, "humidity": 0}, 
    "fan_status": "OFF",
    "notification": ""
}

# ==========================================
# BACKGROUND THREADS (Email, MQTT, BLE)
# ==========================================

def fetch_pareto_data():
    target_mac = "c30000455da6/3"
    url = f"http://localhost:3001/context/device/{target_mac}"
    
    while True:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                device_info = data.get("devices", {}).get(target_mac, {})
                dynamb_data = device_info.get("dynamb", {})
                
                if "temperature" in dynamb_data and "relativeHumidity" in dynamb_data:
                    temp = round(dynamb_data["temperature"], 1)
                    hum = round(dynamb_data["relativeHumidity"], 1)
                    sensor_state["Frig_BT"]["temperature"] = temp
                    sensor_state["Frig_BT"]["humidity"] = hum
                    
                    # Passes data to the master evaluator
                    evaluate_cooling_system("Frig_BT", temp)
        except Exception:
            pass
        time.sleep(2)
        
def on_connect(client, userdata, flags, rc):
    client.subscribe([("Frig1", 0), ("Frig2", 0), ("FanStatus", 0), ("SystemNotification", 0)])

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()
    
    if topic in ["Frig1", "Frig2", "Frig_BT"]: 
        try:
            data = json.loads(payload)
            temp = float(data.get('temperature', 0))
            sensor_state[topic]['temperature'] = temp
            sensor_state[topic]['humidity'] = data.get('humidity', 0)

            # Passes data to the master evaluator
            evaluate_cooling_system(topic, temp)
        except:
            pass
    elif topic == "FanStatus":
        sensor_state['fan_status'] = payload
    elif topic == "SystemNotification":
        sensor_state['notification'] = payload

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def start_mqtt():
    try:
        mqtt_client.connect("127.0.0.1", 1883, 60)
        mqtt_client.loop_forever()
    except Exception as e:
        print(f"MQTT Error: {e}")

# Start all background threads
threading.Thread(target=start_mqtt, daemon=True).start()
threading.Thread(target=fetch_pareto_data, daemon=True).start()
threading.Thread(target=listen_for_email_commands, daemon=True).start()

# ==========================================
# WEB ROUTES
# ==========================================

@app.route('/api/sensors')
def api_sensors():
    return jsonify(sensor_state)

@app.route('/api/update_threshold', methods=['POST'])
def update_threshold():
    if not session.get('admin'):
        return jsonify({"error": "Unauthorized"}), 403
        
    data = request.json
    fridge = data.get('fridge')
    try:
        value = float(data.get('value'))
    except (TypeError, ValueError):
        value = 25.0
    
    # FIX: Update the specific threshold limit, NOT the simulated temperature!
    if fridge in fridge_thresholds:
        fridge_thresholds[fridge] = value
        
    mqtt_client.publish(f"UpdateThreshold/{fridge}", str(value))
    
    # Grab the current temperature reading and instantly re-evaluate the system
    current_temp = sensor_state[fridge]['temperature']
    evaluate_cooling_system(fridge, current_temp)
        
    return jsonify({"success": True})

@app.route('/dashboard')
def iot_dashboard():
    if not session.get('admin'):
        flash("Admin access required.", "alert-danger")
        return redirect(url_for('login'))
    return render_template('dashboard.html')
    
@app.route('/')
def checkout():
    if 'cart' not in session:
        session['cart'] = []
    cart = session['cart']
    total = sum(item['qty'] * item['price'] for item in cart)
    
    conn = get_db_connection()
    inventory = conn.execute('SELECT * FROM products').fetchall()
    conn.close()
    return render_template('checkout.html', cart=cart, total=total, inventory=inventory)

@app.route('/bulk_rfid_scan', methods=['POST'])
def bulk_rfid_scan():
    scanned_epcs = read_bulk_rfid()
    if not scanned_epcs:
        flash("No RFID tags detected in the basket.", "alert-warning")
        return redirect(url_for('checkout'))

    conn = get_db_connection()
    cart = session.get('cart', [])
    items_added = 0
    
    for epc in scanned_epcs:
        product = conn.execute('SELECT * FROM products WHERE epc = ?', (epc,)).fetchone()
        if product:
            item_found = False
            for item in cart:
                if item['epc'] == epc:
                    item['qty'] += 1
                    item_found = True
                    break
            if not item_found:
                cart.append({"epc": product['epc'], "name": product['name'], "qty": 1, "price": product['price']})
            items_added += 1
            
    conn.close()
    session['cart'] = cart 
    
    if items_added > 0:
        flash(f"Basket Scan: {items_added} items added!", "alert-success")
    else:
        flash("Tags detected, but none matched the database.", "alert-danger")

    return redirect(url_for('checkout'))

def send_receipt_email(user_email, user_name, receipt_id, total, items):
    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        sender_email = os.getenv("SENDER_EMAIL") 
        sender_password = os.getenv("EMAIL_PASS") 

        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = user_email
        message["Subject"] = f"Your Smart Store Receipt - #{receipt_id}"

        item_list_str = ""
        for item in items:
            name = item.get('name', 'Unknown Item')
            price = item.get('price', 0.0)
            epc = item.get('epc', 'No EPC')
            item_list_str += f"• {name} - ${float(price):.2f}\n  (Tag ID: {epc})\n\n"

        body = f"Hello {user_name},\n\nThank you for shopping at the Smart Store!\n\nOrder Details:\n-------------------------------------------\nReceipt ID: {receipt_id}\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nItems:\n{item_list_str}-------------------------------------------\nTOTAL PAID: ${float(total):.2f}\n\nYour items have been removed from our active inventory."
        
        message.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(message)
        server.quit()
        return True
    except Exception as e:
        print(f"DEBUG EMAIL ERROR: {e}")
        return False
    
@app.route('/pay', methods=['POST'])
def pay():
    cart = session.get('cart', [])
    if not cart:
        flash("Your cart is empty!", "alert-warning")
        return redirect(url_for('checkout'))

    user_email = session.get('user_email', 'send.abdulmajeed@gmail.com') 
    user_name = session.get('user_name', 'Guest Customer')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    total_price = sum(float(item['price']) for item in cart)
    points_earned = int(total_price * 10) 
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('INSERT INTO receipts (customer_id, total, points, date_time) VALUES (?, ?, ?, ?)',
                   (session.get('user_id', 1), total_price, points_earned, current_time))
    receipt_id = cursor.lastrowid
    
    for item in cart:
        cursor.execute('INSERT INTO receipt_items (receipt_id, epc, name, price) VALUES (?, ?, ?, ?)',
                       (receipt_id, item['epc'], item['name'], item['price']))
        cursor.execute('UPDATE inventory SET status = "sold" WHERE epc = ?', (item['epc'],))
    
    conn.commit()
    conn.close()

    send_receipt_email(user_email, user_name, receipt_id, total_price, cart)

    session['cart'] = []
    session.modified = True
    return redirect(url_for('checkout'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session or 'admin' in session:
        return redirect(url_for('history'))

    if request.method == 'POST':
        email = request.form.get('email')
        membership_num = request.form.get('membership_num')

        if email == "admin@smartstore.com" and membership_num == "admin":
            session['admin'] = True
            session['user_name'] = "Admin"
            flash("Admin logged in successfully!", "modal-success")
            return redirect(url_for('products'))

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM customers WHERE email = ? AND membership_num = ?', 
                            (email, membership_num)).fetchone()
        conn.close()

        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            session['membership_num'] = user['membership_num']
            flash("Logged in successfully!", "modal-success")
            return redirect(url_for('history'))
        else:
            flash("Invalid email or membership number.", "modal-danger")
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/signup', methods=['POST'])
def signup():
    name = request.form.get('name')
    email = request.form.get('email')
    membership_num = str(random.randint(100000, 999999)) 
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO customers (name, email, membership_num) VALUES (?, ?, ?)', 
                       (name, email, membership_num))
        conn.commit()
        
        user_id = cursor.lastrowid
        session['user_id'] = user_id
        session['user_name'] = name
        session['user_email'] = email
        session['membership_num'] = membership_num
        
        flash(f"Account created! Your membership number is {membership_num}", "modal-success")
    except sqlite3.Error:
        flash("An error occurred during sign up.", "modal-danger")
    finally:
        conn.close()
        
    return redirect(url_for('history'))

@app.route('/logout')
def logout():
    session.clear() 
    flash("Logged out successfully.", "alert-info")
    return redirect(url_for('login'))

@app.route('/api/scan_single_tag')
def api_scan_single_tag():
    if not session.get('admin'):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.2)
        ser.reset_input_buffer() 
        
        start_time = time.time()
        last_poll = 0
        raw_hex_stream = ""
        
        while time.time() - start_time < 2.0:
            if time.time() - last_poll > 0.1:
                ser.write(bytes.fromhex("0008220000000022"))
                last_poll = time.time()
                
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                raw_hex_stream += data.hex().upper()
                
        ser.close()
        
        match = re.search(r'(?:A0|E0)([A-F0-9]{22})', raw_hex_stream)
        if match:
            epc = match.group(1) 
            return jsonify({"success": True, "epc": epc})
                    
        return jsonify({"success": False, "error": "No valid tag detected. Make sure it's an A0 or E0 tag."})

    except Exception as e:
        return jsonify({"success": False, "error": f"Hardware Error: {e}"})
 
    
@app.route('/products', methods=['GET', 'POST'])
def products():
    if not session.get('admin'):
        return redirect(url_for('login'))

    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_product':
            try:
                conn.execute('INSERT INTO products (upc, name, price, category_id) VALUES (?, ?, ?, ?)',
                             (request.form.get('upc'), request.form.get('name'), request.form.get('price'), request.form.get('category_id')))
            except sqlite3.IntegrityError:
                flash("Error: A product with that UPC already exists!", "alert-danger")
                
        elif action == 'add_tag':
            try:
                conn.execute('INSERT INTO inventory (epc, upc) VALUES (?, ?)',
                             (request.form.get('epc'), request.form.get('upc')))
            except sqlite3.IntegrityError:
                flash("Database Error: That exact EPC tag is already registered to an item!", "alert-danger")
        
        elif action == 'delete_product':
            upc_to_delete = request.form.get('upc')
            conn.execute('DELETE FROM inventory WHERE upc = ?', (upc_to_delete,))
            conn.execute('DELETE FROM products WHERE upc = ?', (upc_to_delete,))    
        conn.commit()

    inventory_data = conn.execute('''
        SELECT p.upc, p.name, p.price, c.name as category,
        (SELECT COUNT(*) FROM inventory WHERE upc = p.upc AND status = 'available') as stock
        FROM products p
        JOIN categories c ON p.category_id = c.id
    ''').fetchall()

    categories = conn.execute('SELECT * FROM categories').fetchall()
    tags = conn.execute('SELECT * FROM inventory').fetchall()
    conn.close()
    
    return render_template('products.html', products=inventory_data, categories=categories, tags=tags)

@app.route('/admin/reports')
def admin_reports():
    if not session.get('admin'):
        flash("Admin access required.", "alert-danger")
        return redirect(url_for('login'))

    conn = get_db_connection()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    date_filter = ""
    params = []
    if start_date and end_date:
        date_filter = " WHERE date_time >= ? AND date_time <= ? "
        params.extend([start_date + " 00:00:00", end_date + " 23:59:59"])

    revenue_query = f"SELECT SUM(total) as revenue FROM receipts {date_filter}"
    total_revenue_row = conn.execute(revenue_query, params).fetchone()
    total_revenue = total_revenue_row['revenue'] if total_revenue_row['revenue'] else 0.0

    items_query = f'''
        SELECT ri.name, COUNT(ri.epc) as sold_count
        FROM receipt_items ri
        JOIN receipts r ON ri.receipt_id = r.id
        {date_filter}
        GROUP BY ri.name
        ORDER BY sold_count DESC
    '''
    item_sales = conn.execute(items_query, params).fetchall()
    
    top_items = item_sales[:3] if item_sales else []
    bottom_items = item_sales[-3:] if len(item_sales) > 3 else item_sales

    trends_query = f'''
        SELECT date(date_time) as sale_date, SUM(total) as daily_revenue
        FROM receipts
        {date_filter}
        GROUP BY sale_date
        ORDER BY sale_date ASC
    '''
    sales_trends = conn.execute(trends_query, params).fetchall()

    inventory_data = conn.execute('''
        SELECT p.name, p.upc, COUNT(i.epc) as available_stock
        FROM products p
        LEFT JOIN inventory i ON p.upc = i.upc AND i.status = 'available'
        GROUP BY p.upc
    ''').fetchall()

    unique_customers = conn.execute('SELECT COUNT(DISTINCT customer_id) as count FROM receipts').fetchone()['count']
    conn.close()

    return render_template('admin_reports.html', inventory=inventory_data, revenue=total_revenue,
                           item_sales=item_sales, top_items=top_items, bottom_items=bottom_items,
                           sales_trends=sales_trends, unique_customers=unique_customers,
                           start_date=start_date, end_date=end_date)

@app.route('/admin/export_csv')
def export_csv():
    if not session.get('admin'):
        flash("Admin access required.", "alert-danger")
        return redirect(url_for('login'))

    conn = get_db_connection()
    inventory_data = conn.execute('''
        SELECT p.name, p.upc, COUNT(i.epc) as available_stock
        FROM products p
        LEFT JOIN inventory i ON p.upc = i.upc AND i.status = 'available'
        GROUP BY p.upc
    ''').fetchall()

    total_revenue_row = conn.execute('SELECT SUM(total) as revenue FROM receipts').fetchone()
    total_revenue = total_revenue_row['revenue'] if total_revenue_row['revenue'] else 0.0

    unique_customers = conn.execute('SELECT COUNT(DISTINCT customer_id) as count FROM receipts').fetchone()['count']
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Smart Store Admin Report'])
    cw.writerow([])
    cw.writerow(['Total Revenue', f"${total_revenue:.2f}"])
    cw.writerow(['Unique Customers', unique_customers])
    cw.writerow([]) 
    cw.writerow(['--- Current Inventory ---'])
    cw.writerow(['Product Name', 'UPC', 'Available Stock'])
    for item in inventory_data:
        cw.writerow([item['name'], item['upc'], item['available_stock']])

    output = si.getvalue()
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=SmartStore_RawData.csv"})

@app.route('/history')
def history():
    if 'user_id' not in session:
        flash("Please log in.", "alert-danger")
        return redirect(url_for('login'))

    customer_id = session['user_id']
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    search_query = request.args.get('search', '').strip()

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    total_points = user['total_points'] if user and 'total_points' in user.keys() else 0

    base_query = '''
        SELECT r.id, r.date_time, r.total, ri.name, ri.price, ri.epc 
        FROM receipts r
        JOIN receipt_items ri ON r.id = ri.receipt_id
        WHERE r.customer_id = ?
    '''
    params = [customer_id]

    trend_query = '''
        SELECT date(date_time) as sale_date, SUM(total) as daily_spent
        FROM receipts
        WHERE customer_id = ?
    '''
    trend_params = [customer_id]

    if start_date and end_date:
        base_query += " AND r.date_time >= ? AND r.date_time <= ?"
        trend_query += " AND date_time >= ? AND date_time <= ?"
        params.extend([start_date + " 00:00:00", end_date + " 23:59:59"])
        trend_params.extend([start_date + " 00:00:00", end_date + " 23:59:59"])

    trend_query += " GROUP BY sale_date ORDER BY sale_date ASC"
    spending_trends = conn.execute(trend_query, trend_params).fetchall()

    if search_query:
        base_query += " AND ri.name LIKE ?"
        params.append(f"%{search_query}%")

    base_query += " ORDER BY r.date_time DESC"
    raw_items = conn.execute(base_query, params).fetchall()
    total_spent = sum(item['price'] for item in raw_items)

    search_details = []
    if search_query:
        for item in raw_items:
            if search_query.lower() in item['name'].lower():
                search_details.append({
                    'name': item['name'],
                    'date_time': item['date_time'],
                    'price': item['price']
                })

    receipts = {}
    for item in raw_items:
        r_id = item['id']
        if r_id not in receipts:
            receipts[r_id] = {
                'date_time': item['date_time'],
                'total': item['total'],
                'points': int(item['total']),
                'items': []
            }
        receipts[r_id]['items'].append({
            'name': item['name'],
            'price': item['price'],
            'epc': item['epc']
        })

    conn.close()
    return render_template('history.html', receipts=receipts, total_spent=total_spent,
                           total_points=total_points, start_date=start_date, end_date=end_date,
                           search_query=search_query, search_details=search_details,
                           spending_trends=spending_trends)

@app.route('/simulate_scan', methods=['POST'])
def simulate_scan():
    scanned_epc = request.form.get('epc')
    conn = get_db_connection()
    
    product = conn.execute('''
        SELECT i.epc, p.name, p.price, i.status 
        FROM inventory i
        JOIN products p ON i.upc = p.upc
        WHERE i.epc = ?
    ''', (scanned_epc,)).fetchone()
    
    conn.close()
    
    if product:
        if product['status'] != 'available':
            flash(f"Error: That specific {product['name']} tag has already been sold.", "alert-warning")
            return redirect(url_for('checkout'))

        cart = session.get('cart', [])
        if any(item['epc'] == scanned_epc for item in cart):
            flash("This exact item is already in your cart!", "alert-info")
        else:
            cart.append({"epc": product['epc'], "name": product['name'], "qty": 1, "price": product['price']})
            session['cart'] = cart 
            flash(f"{product['name']} scanned successfully!", "alert-success")
    else:
        flash("Unknown Tag. Please link this EPC to a product in the Admin panel.", "alert-danger")

    return redirect(url_for('checkout'))

@app.route('/physical_scan', methods=['POST'])
def physical_scan():
    scanned_upc = request.form.get('epc') 
    if scanned_upc: scanned_upc = scanned_upc.strip().upper()
        
    if not scanned_upc:
        flash("No barcode detected.", "alert-warning")
        return redirect(url_for('checkout'))

    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE upc = ?', (scanned_upc,)).fetchone()
    
    if not product:
        conn.close()
        flash("Unknown barcode. Please register this product in the Admin panel.", "alert-danger")
        return redirect(url_for('checkout'))
        
    cart = session.get('cart', [])
    epcs_in_cart = [item['epc'] for item in cart]
    
    query = "SELECT epc FROM inventory WHERE upc = ? AND status = 'available'"
    params = [scanned_upc]
    
    if epcs_in_cart:
        placeholders = ','.join(['?'] * len(epcs_in_cart))
        query += f" AND epc NOT IN ({placeholders})"
        params.extend(epcs_in_cart)
        
    available_item = conn.execute(query, params).fetchone()
    conn.close()
    
    if available_item:
        cart.append({"epc": available_item['epc'], "name": product['name'], "qty": 1, "price": product['price']})
        session['cart'] = cart
        session.modified = True
        flash(f"Barcode Scan: {product['name']} added!", "alert-success")
    else:
        flash(f"Sorry, {product['name']} is out of stock or missing inventory tags!", "alert-warning")

    return redirect(url_for('checkout'))

@app.route('/clear_cart')
def clear_cart():
    session.pop('cart', None)
    return redirect(url_for('checkout'))

@app.route('/api/auto_scan', methods=['POST'])
def api_auto_scan():
    raw_hex_stream = read_bulk_rfid()
    conn = get_db_connection()
    cart = session.get('cart', [])
    added = 0
    
    available_tags = conn.execute('''
        SELECT i.epc, p.name, p.price 
        FROM inventory i 
        JOIN products p ON i.upc = p.upc 
        WHERE i.status = 'available'
    ''').fetchall()
    
    for tag in available_tags:
        epc = tag['epc']
        if epc in raw_hex_stream:
            if not any(item['epc'] == epc for item in cart):
                cart.append({"epc": epc, "name": tag['name'], "qty": 1, "price": tag['price']})
                added += 1
                print(f"BINGO! Extracted {tag['name']} ({epc}) from raw stream!")
                
    if added > 0:
        session['cart'] = cart
        session.modified = True 
        
    conn.close()
    return {"status": "success" if added > 0 else "ignored", "items_added": added}

@app.route('/remove_from_cart/<epc>')
def remove_from_cart(epc):
    cart = session.get('cart', [])
    session['cart'] = [item for item in cart if item['epc'] != epc]
    session.modified = True
    return redirect(url_for('checkout'))

if __name__ == '__main__':
    # use_reloader=False stops Flask from duplicating the GPIO processes!
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)