import os
import io
from bson.objectid import ObjectId
from bson import json_util
from dotenv import load_dotenv
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify, Response
from pymongo import MongoClient
from werkzeug.utils import secure_filename
from PIL import Image, ImageChops, ImageEnhance
from PIL.ExifTags import TAGS
import numpy as np
import cv2

load_dotenv()
app = Flask(__name__)
# Securely handle secret key for sessions
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))

# --- Configuration ---
MONGO_URI = os.getenv('MONGO_URI')
client = MongoClient(MONGO_URI)
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = client['project']
users_collection = db['User']
descriptions_collection = db['Admin']
information_collection = db['Infromation']

# --- HELPER 1: EXIF ANALYSIS ---
def analyze_exif_data(filepath):
    try:
        image = Image.open(filepath)
        exif_data = image.getexif()
        if not exif_data:
            return {'status': 'Clean', 'details': 'No EXIF metadata found.'}
        software_tag = 305 # Software tag ID
        if software_tag in exif_data:
            return {'status': 'Editing Software Found', 'details': f'Software: {exif_data[software_tag]}'}
        return {'status': 'Clean', 'details': 'No editing software tags found.'}
    except Exception as e:
        return {'status': 'Error', 'details': f'Could not read EXIF data: {str(e)}'}

# --- HELPER 2: ELA HEATMAP GENERATION ---
def generate_ela_heatmap(filepath, filename):
    try:
        original = Image.open(filepath).convert('RGB')
        temp_buffer = io.BytesIO()
        # Save at a specific quality to detect subsequent re-saves
        original.save(temp_buffer, "JPEG", quality=90)
        temp_buffer.seek(0)
        resaved = Image.open(temp_buffer)
        
        # Calculate pixel difference
        ela_image = ImageChops.difference(original, resaved)
        extrema = ela_image.getextrema()
        max_diff = max([ex[1] for ex in extrema])
        if max_diff == 0: max_diff = 1
        scale = 255.0 / max_diff
        enhanced_ela = ImageEnhance.Brightness(ela_image).enhance(scale)
        
        heatmap_filename = f"ela_{filename}"
        heatmap_path = os.path.join(app.config['UPLOAD_FOLDER'], heatmap_filename)
        enhanced_ela.save(heatmap_path)
        
        ela_array = np.array(ela_image)
        std_dev = np.std(ela_array)
        ELA_THRESHOLD = 15.0 
        status = "High Variance" if std_dev > ELA_THRESHOLD else "Low Variance"
        return {'status': status, 'details': f"Std Dev: {std_dev:.2f}", 'heatmap_url': url_for('static', filename=f'uploads/{heatmap_filename}')}
    except Exception as e:
        return {'status': 'Error', 'details': f'Failed to perform ELA: {str(e)}', 'heatmap_url': None}

# --- HELPER 3: COPY-MOVE FORGERY DETECTION ---
def analyze_copy_move(filepath, sensitivity=20):
    try:
        img = cv2.imread(filepath, 0)
        if img is None: return {'status': 'Error', 'details': 'OpenCV could not open image.'}
        orb = cv2.ORB_create()
        keypoints, descriptors = orb.detectAndCompute(img, None)
        if descriptors is None or len(descriptors) < 2:
            return {'status': 'No Clones Detected', 'details': 'Not enough features to compare.'}
        
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(descriptors, descriptors)
        # Filter matches that aren't the exact same point but have identical descriptors
        good_matches = [m for m in matches if m.queryIdx != m.trainIdx and np.sqrt((keypoints[m.queryIdx].pt[0] - keypoints[m.trainIdx].pt[0])**2 + (keypoints[m.queryIdx].pt[1] - keypoints[m.trainIdx].pt[1])**2) > 20]
        
        if len(good_matches) > sensitivity:
            return {'status': "Cloned Regions Detected", 'details': f"Found {len(good_matches)} matching feature pairs."}
        return {'status': "No Clones Detected", 'details': f"Found {len(good_matches)} matching feature pairs."}
    except Exception as e:
        return {'status': 'Error', 'details': f'Failed to perform CMFD: {str(e)}'}

# --- HELPER 4: NOISE ANALYSIS ---
def generate_noise_map(filepath, filename):
    try:
        img = cv2.imread(filepath)
        if img is None: return {'status': 'Error', 'details': 'Could not open image for noise analysis.', 'map_url': None}
        img_ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        y_channel = img_ycrcb[:,:,0]
        denoised = cv2.medianBlur(y_channel, 3)
        noise = cv2.subtract(y_channel, denoised)
        cv2.normalize(noise, noise, 0, 255, cv2.NORM_MINMAX)
        colored_noise_map = cv2.applyColorMap(noise, cv2.COLORMAP_JET)
        
        map_filename = f"noise_{filename}"
        map_path = os.path.join(app.config['UPLOAD_FOLDER'], map_filename)
        cv2.imwrite(map_path, colored_noise_map)
        
        std_dev = np.std(noise)
        status = "Inconsistent Noise" if std_dev > 8.5 else "Consistent Noise"
        return {'status': status, 'details': f"Std Dev: {std_dev:.2f}", 'map_url': url_for('static', filename=f'uploads/{map_filename}')}
    except Exception as e:
        return {'status': 'Error', 'details': f'Failed to perform Noise Analysis: {str(e)}', 'map_url': None}

# --- HELPER 5: FINAL DECISION LOGIC ---
def make_final_decision(exif, ela, cmfd, noise):
    tamper_score = 0
    summary_points = []
    
    if cmfd['status'] == 'Cloned Regions Detected':
        tamper_score += 10
        summary_points.append("High risk of damage exaggeration (clones found)")
    if exif['status'] == 'Editing Software Found':
        tamper_score += 10
        summary_points.append("EXIF metadata confirms editing software")
    if noise['status'] == 'Inconsistent Noise':
        tamper_score += 5
        summary_points.append("Inconsistent noise pattern (possible inpainting)")
    if ela['status'] == 'High Variance':
        tamper_score += 2
        summary_points.append("ELA shows compression inconsistencies")

    if tamper_score >= 10: status, confidence = 'Tampered', 'High'
    elif tamper_score >= 5: status, confidence = 'Tampered', 'Medium'
    elif tamper_score >= 2: status, confidence = 'Potentially Tampered', 'Low'
    else:
        status, confidence = 'Authentic', 'High'
        summary_points.append("No clear evidence of tampering detected")
        
    return {'status': status, 'confidence': confidence, 'summary': ". ".join(summary_points) + "."}

# --- ROUTES ---
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        if username == 'admin123' and password == 'admin123':
            session['username'], session['is_admin'] = 'admin', True
            return redirect(url_for('dashboard'))
            
        user = users_collection.find_one({'username': username, 'password': password})
        if user:
            session['username'], session['is_admin'] = user['username'], False
            return redirect(url_for('ui_page'))
        return render_template('login.html', error="Invalid credentials.")
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        confirm = request.form['confirm_password'].strip()
        
        if users_collection.find_one({'username': username}):
            return render_template('signup.html', error="Username exists.")
        if password != confirm:
            return render_template('signup.html', error="Passwords mismatch.")
            
        users_collection.insert_one({'username': username, 'password': password})
        flash('Success! Please log in.')
        return redirect(url_for('home'))
    return render_template('signup.html')

@app.route('/ui')
def ui_page():
    if 'username' in session and not session.get('is_admin'):
        submission = descriptions_collection.find_one({'username': session['username']})
        if submission:
            return redirect(url_for('status_page', submission_id=str(submission['_id'])))
        return render_template('ui.html', user=session['username'])
    return redirect(url_for('home'))

@app.route('/submit_description', methods=['POST'])
def submit_description():
    if 'username' not in session: return "Unauthorized", 401
    image_file = request.files['image_file']
    if image_file and image_file.filename != '':
        filename = secure_filename(image_file.filename)
        image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        new_submission = descriptions_collection.insert_one({
            'username': session['username'],
            'registration_number': request.form['registration_number'],
            'description': request.form['description'],
            'image_filename': filename,
            'status': 'Submitted'
        })
        return redirect(url_for('status_page', submission_id=new_submission.inserted_id))
    return 'Upload error', 500

@app.route('/status/<submission_id>')
def status_page(submission_id):
    if 'username' not in session: return redirect(url_for('home'))
    try:
        query = {'_id': ObjectId(submission_id)}
        if not session.get('is_admin'): query['username'] = session['username']
        submission = descriptions_collection.find_one(query)
        if not submission: return "Not found", 404
        return render_template('status.html', submission=submission)
    except: return "Invalid ID", 400

@app.route('/dashboard')
def dashboard():
    if 'username' not in session or not session.get('is_admin'): return redirect(url_for('home'))
    # Fetch lists for Jinja and JSON for JS
    pending = list(descriptions_collection.find({'status': {'$in': ['Submitted', 'Under Review']}}))
    completed = list(descriptions_collection.find({'status': {'$in': ['Approved', 'Rejected']}}))
    return render_template('dashboard.html', 
                            pending_requests=pending,
                            completed_requests=completed,
                            pending_requests_json=json_util.dumps(pending), 
                            completed_requests_json=json_util.dumps(completed))

@app.route('/get_documents/<registration_number>', methods=['GET'])
def get_documents(registration_number):
    if 'username' not in session or not session.get('is_admin'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        # Note: Querying by 'Registration Number' to match your DB key
        documents = list(information_collection.find({'Registration Number': registration_number}))
        return Response(json_util.dumps(documents), mimetype='application/json')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/mark_as_under_review/<submission_id>', methods=['POST'])
def mark_as_under_review(submission_id):
    if 'username' not in session or not session.get('is_admin'): return jsonify({'success': False}), 401
    descriptions_collection.update_one({'_id': ObjectId(submission_id)}, {'$set': {'status': 'Under Review'}})
    return jsonify({'success': True})

@app.route('/update_status/<submission_id>', methods=['POST'])
def update_status(submission_id):
    if 'username' not in session or not session.get('is_admin'): return jsonify({'success': False}), 401
    data = request.get_json()
    new_status = data.get('status')
    claim_amount = data.get('claimAmount')
    
    update_data = {'status': new_status}
    if new_status == 'Approved' and claim_amount:
        update_data['claimAmount'] = float(claim_amount)
    
    result = descriptions_collection.update_one({'_id': ObjectId(submission_id)}, {'$set': update_data})
    return jsonify({'success': result.modified_count > 0})

@app.route('/advanced_analysis/<filename>')
def advanced_analysis(filename):
    if 'username' not in session or not session.get('is_admin'): return jsonify({'error': 'Unauthorized'}), 401
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath): return jsonify({'error': 'File not found'}), 404
    
    exif = analyze_exif_data(filepath)
    ela = generate_ela_heatmap(filepath, filename)
    cmfd = analyze_copy_move(filepath)
    noise = generate_noise_map(filepath, filename)
    verdict = make_final_decision(exif, ela, cmfd, noise)

    return jsonify({
        'final_verdict': verdict,
        'exif_analysis': exif,
        'ela_analysis': ela,
        'cmfd_analysis': cmfd,
        'noise_analysis': noise
    })

if __name__ == "__main__":
    # CRITICAL: use_reloader=False and threaded=False to prevent Python 3.14 socket errors
    app.run(debug=True, use_reloader=False, threaded=False)