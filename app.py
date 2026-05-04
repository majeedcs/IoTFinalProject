import os
import sqlite3
import random
import smtplib
from datetime import datetime
import serial
import time
import binascii
import select 
from flask import Flask, render_template, request, flash, redirect, url_for, session
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import csv
from io import StringIO
from flask import Response
import paho.mqtt.client as mqtt
import threading
import json
from flask import jsonify, request, render_template, session, flash, redirect, url_for
load_dotenv()

import requests
import time
import threading

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key")

def get_db_connection():
    conn = sqlite3.connect('store.db')
    conn.row_factory = sqlite3.Row 
    return conn

# ==============================================================================
# WINDOWS VERSION (COM Port) - Raw Stream Capture
# ==============================================================================
# def read_bulk_rfid():
#     try:
#         import serial
#         import time
#         # Increased timeout slightly for better stability
#         ser = serial.Serial("COM3", 115200, timeout=0.2)
#         ser.reset_input_buffer() 
        
#         start_time = time.time()
#         last_poll = 0
#         raw_hex_stream = ""
        
#         while time.time() - start_time < 2.0:
#             if time.time() - last_poll > 0.1:
#                 ser.write(bytes.fromhex("0008220000000022"))
#                 last_poll = time.time()
                
#             # Grab literally whatever is sitting in the USB port and turn it to text
#             if ser.in_waiting > 0:
#                 data = ser.read(ser.in_waiting)
#                 raw_hex_stream += data.hex().upper()
                
#         ser.close()
#         return raw_hex_stream # Return the giant, messy string of raw data

#     except Exception as e:
#         print(f"Hardware Error: {e}")
#         if 'ser' in locals() and ser.is_open:
#             ser.close()
#         return ""

# # ==============================================================================
# # LINUX/RASPBERRY PI VERSION (ttyUSB0) - Standard Serial Method
# # ==============================================================================
def read_bulk_rfid():
    import serial
    import time
    
    try:
        # Connect to the RFID scanner as a standard serial port
        ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.2)
        ser.reset_input_buffer() 
        
        start_time = time.time()
        last_poll = 0
        raw_hex_stream = ""
        
        while time.time() - start_time < 2.0:
            # Send the "Scan" command
            if time.time() - last_poll > 0.1:
                ser.write(bytes.fromhex("0008220000000022"))
                last_poll = time.time()
                
            # Grab everything sitting in the USB port
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                raw_hex_stream += data.hex().upper()
                
        ser.close()
        
        # RETURN THE GIANT STRING EXACTLY LIKE WINDOWS DID
        return raw_hex_stream 

    except Exception as e:
        print(f"RFID Hardware Error: {e}")
        return ""
    
def normalize_epc(raw_epc):
    """
    Cleans factory tags into a standard 22-character format.
    Example: 0000CF0100011200FC7C01 -> 0000000000000000007C
    """
    if not raw_epc:
        return ""
        
    # 1. Remove the last 2 digits (the noise/checksum you wanted gone)
    # This turns ...7C01 into ...7C
    clean_id = raw_epc[:-2] 
    
    # 2. If it's a factory 'CF' tag, strip the long prefix
    # We look for the last 4 characters of the remaining string as the unique ID
    if clean_id.startswith("0000CF"):
        unique_part = clean_id[-4:] # Takes the '7C' or '4590' part
    else:
        # If it's already a '0000' tag, just take the end
        unique_part = clean_id[-4:]

    # 3. Pad with 18 zeros to make it exactly 22 characters
    standardized_epc = unique_part.zfill(22)
    
    return standardized_epc    

# Update your existing sensor_state to ensure Frig_BT is ready
sensor_state = {
    "Frig1": {"temperature": 0, "humidity": 0},
    "Frig2": {"temperature": 0, "humidity": 0},
    "Frig_BT": {"temperature": 0, "humidity": 0}, 
    "fan_status": "OFF",
    "notification": ""
}

import requests
import time
import threading

def fetch_pareto_data():
    # Make sure this MAC address matches YOUR exact sensor (da6 vs da7)
    target_mac = "c30000455da6/3"
    url = f"http://localhost:3001/context/device/{target_mac}"
    
    print("🔗 Starting Pareto Thread (Looking for 'dynamb')...", flush=True)
    
    while True:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                
                device_info = data.get("devices", {}).get(target_mac, {})
                
                if not device_info:
                    print(f" Device {target_mac} not found in PA response.", flush=True)
                    time.sleep(2)
                    continue

                # THE TEAMMATE FIX: Look for 'dynamb' instead of 'dyn'
                dynamb_data = device_info.get("dynamb", {})
                
                # Check for the keys exactly as your teammate wrote them
                if "temperature" in dynamb_data and "relativeHumidity" in dynamb_data:
                    temp = round(dynamb_data["temperature"], 1)
                    hum = round(dynamb_data["relativeHumidity"], 1)
                    
                    # Update your Flask dictionary
                    sensor_state["Frig_BT"]["temperature"] = temp
                    sensor_state["Frig_BT"]["humidity"] = hum
                    print(f" BLE Update: Temp {temp}°C, Hum {hum}%", flush=True)
                else:
                    print(f" Found device, but missing dynamb data. Pareto sent: {dynamb_data}", flush=True)
            else:
                print(f" API Error: HTTP {response.status_code}", flush=True)
                
        except Exception as e:
            print(f" Connection Failed: {e}", flush=True)
            
        time.sleep(2)
        
def on_connect(client, userdata, flags, rc):
    client.subscribe("Frig1")
    client.subscribe("Frig2")
    client.subscribe("FanStatus")
    client.subscribe("SystemNotification")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()
    
    if topic in ["Frig1", "Frig2", "Frig_BT"]: # Added Frig_BT here
        try:
            data = json.loads(payload)
            sensor_state[topic]['temperature'] = data.get('temperature', 0)
            sensor_state[topic]['humidity'] = data.get('humidity', 0)
        except:
            pass
    elif topic == "FanStatus":
        sensor_state['fan_status'] = payload
    elif topic == "SystemNotification":
        sensor_state['notification'] = payload

#mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1) 
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def start_mqtt():
    try:
        mqtt_client.connect("127.0.0.1", 1883, 60)
        mqtt_client.loop_forever()
    except Exception as e:
        print(e)

threading.Thread(target=start_mqtt, daemon=True).start()
threading.Thread(target=fetch_pareto_data, daemon=True).start()
@app.route('/api/sensors')
def api_sensors():
    return jsonify(sensor_state)

@app.route('/api/update_threshold', methods=['POST'])
def update_threshold():
    if not session.get('admin'):
        return jsonify({"error": "Unauthorized"}), 403
        
    data = request.json
    fridge = data.get('fridge')
    value = data.get('value')
    
    mqtt_client.publish(f"UpdateThreshold/{fridge}", str(value))
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
        flash("No RFID tags detected in the basket. / Aucun tag détecté.", "alert-warning")
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
                cart.append({
                    "epc": product['epc'],
                    "name": product['name'],
                    "qty": 1,
                    "price": product['price']
                })
            items_added += 1
            
    conn.close()
    session['cart'] = cart 
    
    if items_added > 0:
        flash(f"Basket Scan: {items_added} items added! / {items_added} articles ajoutés!", "alert-success")
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

        body = f"""
        Hello {user_name},

        Thank you for shopping at the Smart Store!
        
        Order Details:
        -------------------------------------------
        Receipt ID: {receipt_id}
        Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        
        Items:
        {item_list_str}
        -------------------------------------------
        TOTAL PAID: ${float(total):.2f}
        
        Your items have been removed from our active inventory.
        """
        
        message.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(message)
        server.quit()
        print("DEBUG: Email successfully sent!")
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

    # 1. Grab user info from session BEFORE clearing it
    user_email = session.get('user_email', 'send.abdulmajeed@gmail.com') # Default to your email for testing
    user_name = session.get('user_name', 'Guest Customer')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    total_price = sum(float(item['price']) for item in cart)
    points_earned = int(total_price * 10) 
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 2. Database updates
    cursor.execute('INSERT INTO receipts (customer_id, total, points, date_time) VALUES (?, ?, ?, ?)',
                   (session.get('user_id', 1), total_price, points_earned, current_time))
    receipt_id = cursor.lastrowid
    
    for item in cart:
        cursor.execute('INSERT INTO receipt_items (receipt_id, epc, name, price) VALUES (?, ?, ?, ?)',
                       (receipt_id, item['epc'], item['name'], item['price']))
        cursor.execute('UPDATE inventory SET status = "sold" WHERE epc = ?', (item['epc'],))
    
    conn.commit()
    conn.close()

    user_email = session.get('user_email', 'send.abdulmajeed@gmail.com')
    user_name = session.get('user_name', 'Customer')
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
            flash("Admin logged in successfully! / Admin connecté!", "modal-success")
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
            flash("Logged in successfully! / Connexion réussie!", "modal-success")
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
    except sqlite3.Error as e:
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
    # Security check - only admins can scan inventory into the system
    if not session.get('admin'):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    import serial
    import time
    import re  # We import regex to do a smart search
    
    try:
        ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.2)
        ser.reset_input_buffer() 
        
        start_time = time.time()
        last_poll = 0
        raw_hex_stream = ""
        
        # Scan for 2 seconds
        while time.time() - start_time < 2.0:
            if time.time() - last_poll > 0.1:
                ser.write(bytes.fromhex("0008220000000022"))
                last_poll = time.time()
                
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                raw_hex_stream += data.hex().upper()
                
        ser.close()
        
        # SMART SEARCH: Find A0 or E0, but ONLY keep the 22 characters after it!
        # The parentheses around the 22 characters create a "Group" we can extract.
        match = re.search(r'(?:A0|E0)([A-F0-9]{22})', raw_hex_stream)
        
        if match:
            # match.group(1) drops the A0/E0 prefix and returns just the pure 22-character ID
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
            conn.execute('INSERT INTO products (upc, name, price, category_id) VALUES (?, ?, ?, ?)',
                         (request.form.get('upc'), request.form.get('name'), request.form.get('price'), request.form.get('category_id')))
        
        elif action == 'add_tag':
            try:
                conn.execute('INSERT INTO inventory (epc, upc) VALUES (?, ?)',
                             (request.form.get('epc'), request.form.get('upc')))
                # Optional: You can add a success flash here if you want
            except sqlite3.IntegrityError:
                # This catches the UNIQUE constraint failure!
                flash("Database Error: That exact EPC tag is already registered to an item!", "alert-danger")
        
        elif action == 'delete_product':
            conn.execute('DELETE FROM products WHERE upc = ?', (request.form.get('upc'),))
        
        elif action == 'delete_product':
            upc_to_delete = request.form.get('upc')
            # 1. Delete all physical tags linked to this product first
            conn.execute('DELETE FROM inventory WHERE upc = ?', (upc_to_delete,))
            # 2. Then delete the product itself
            conn.execute('DELETE FROM products WHERE upc = ?', (upc_to_delete,))    
        conn.commit()

    # Get products with their current stock count (Relational Query)
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
        # We append the time to ensure it captures the full end date
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

    return render_template('admin_reports.html',
                           inventory=inventory_data,
                           revenue=total_revenue,
                           item_sales=item_sales,
                           top_items=top_items,
                           bottom_items=bottom_items,
                           sales_trends=sales_trends,
                           unique_customers=unique_customers,
                           start_date=start_date,
                           end_date=end_date)

@app.route('/admin/export_csv')
def export_csv():
    # Security check
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
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=SmartStore_RawData.csv"}
    )

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

    return render_template('history.html', 
                           receipts=receipts, 
                           total_spent=total_spent,
                           total_points=total_points,
                           start_date=start_date, 
                           end_date=end_date,
                           search_query=search_query,
                           search_details=search_details,
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
            cart.append({
                "epc": product['epc'],
                "name": product['name'],
                "qty": 1,
                "price": product['price']
            })
            session['cart'] = cart 
            flash(f"{product['name']} scanned successfully!", "alert-success")
    else:
        flash("Unknown Tag. Please link this EPC to a product in the Admin panel.", "alert-danger")

    return redirect(url_for('checkout'))

@app.route('/physical_scan', methods=['POST'])
def physical_scan():
    scanned_upc = request.form.get('epc') 
    
    if scanned_upc:
        scanned_upc = scanned_upc.strip().upper()
        
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
        cart.append({
            "epc": available_item['epc'], 
            "name": product['name'],
            "qty": 1,
            "price": product['price']
        })
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
    
    # print(f"DEBUG: Scanned raw data length: {len(raw_hex_stream)}")

    #     print("DEBUG: No data received from COM port.")
    #     return {"status": "empty", "items_added": 0}
    
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
                cart.append({
                    "epc": epc, 
                    "name": tag['name'], 
                    "qty": 1, 
                    "price": tag['price']
                })
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
    app.run(debug=True)