from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, emit, leave_room
from datetime import datetime, timezone, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'change_this_secret_key'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=60)

db = SQLAlchemy(app)
jwt = JWTManager(app)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# -----------------------------
# MODELS
# -----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

# Create tables
with app.app_context():
    db.create_all()

# -----------------------------
# HELPER: Consistent room for 2 users
# -----------------------------
def get_room(user1, user2):
    return f"room_{min(user1, user2)}_{max(user1, user2)}"

# Store connected users
connected_users = {}

# -----------------------------
# SOCKET.IO EVENTS
# -----------------------------
@socketio.on("connect")
def handle_connect():
    user_id = request.args.get('userId')
    if user_id:
        connected_users[user_id] = request.sid
        print(f"✅ User {user_id} connected with SID {request.sid}")
        emit("connected", {"message": "Connected to chat server"}, room=request.sid)

@socketio.on("disconnect")
def handle_disconnect():
    user_id = None
    for uid, sid in connected_users.items():
        if sid == request.sid:
            user_id = uid
            break
    if user_id:
        del connected_users[user_id]
    print(f"❌ User disconnected: {request.sid}")

@socketio.on("join")
def handle_join(data):
    try:
        sender_id = int(data['sender_id'])
        receiver_id = int(data['receiver_id'])
        sender_username = data['sender_username']
        
        room = get_room(sender_id, receiver_id)
        join_room(room)
        
        print(f"🔵 User {sender_id} ({sender_username}) joined room {room}")
        
        # Notify both users
        emit("system", {
            "message": f"{sender_username} joined the chat",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }, to=room)
        
        # Send confirmation to sender
        emit("joined_room", {
            "room": room,
            "message": f"You joined chat with user {receiver_id}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }, room=request.sid)
        
    except Exception as e:
        print(f"Error in join: {e}")
        emit("error", {"message": "Failed to join room"}, room=request.sid)

@socketio.on("send_message")
def handle_send_message(data):
    try:
        sender_id = int(data["sender_id"])
        receiver_id = int(data["receiver_id"])
        message_text = data["message"].strip()

        if not message_text:
            emit("error", {"message": "Message cannot be empty"}, room=request.sid)
            return

        # Save message in DB with UTC timestamp
        msg = Message(
            sender_id=sender_id, 
            receiver_id=receiver_id, 
            message=message_text
        )
        db.session.add(msg)
        db.session.commit()

        # Refresh to get the actual timestamp from database
        db.session.refresh(msg)
        
        room = get_room(sender_id, receiver_id)
        payload = {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "message": message_text,
            "timestamp": msg.timestamp.isoformat(),  # Use server timestamp only
            "message_id": msg.id  # Add message ID for tracking
        }

        print(f"📨 Message from {sender_id} to {receiver_id} at {msg.timestamp.isoformat()}: {message_text}")
        
        # Emit to all users in the same room with SERVER timestamp
        emit("receive_message", payload, to=room)
        
        # Send delivery confirmation to sender with SERVER timestamp
        emit("message_sent", {
            "message": "Message delivered",
            "timestamp": msg.timestamp.isoformat(),
            "message_id": msg.id
        }, room=request.sid)
        
    except Exception as e:
        print(f"Error sending message: {e}")
        emit("error", {"message": "Failed to send message"}, room=request.sid)

# -----------------------------
# HISTORY API - Improved
# -----------------------------
@app.route("/messages/<int:user1>/<int:user2>")
def get_messages(user1, user2):
    try:
        msgs = Message.query.filter(
            ((Message.sender_id == user1) & (Message.receiver_id == user2)) |
            ((Message.sender_id == user2) & (Message.receiver_id == user1))
        ).order_by(Message.timestamp.asc()).all()

        return jsonify([
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "receiver_id": m.receiver_id,
                "message": m.message,
                "timestamp": m.timestamp.isoformat()  # Consistent UTC timestamp
            }
            for m in msgs
        ])
    except Exception as e:
        print(f"Error fetching messages: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

# -----------------------------
# HEALTH CHECK
# -----------------------------
@app.route("/health")
def health_check():
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC+5:45"
    })

# -----------------------------
# RUN
# -----------------------------
if __name__ == '__main__':
    print(" Starting Chat Server on port 5001...")
    print("Using UTC timezone for all timestamps")
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)