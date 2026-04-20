"""routers/auth.py - Login/Register/Password with tabs field"""
import csv, hashlib, datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.paths import PATHS
from core.notify import send_to_admins

router = APIRouter(prefix="/api/auth", tags=["auth"])

FIELDS = ["username","password_hash","role","status","created","tabs"]

class LoginReq(BaseModel):
    username: str
    password: str

class RegisterReq(BaseModel):
    username: str
    password: str

class ResetReq(BaseModel):
    username: str

class ChangePwReq(BaseModel):
    username: str
    old_password: str
    new_password: str

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def read_users():
    users = []
    if PATHS.users_csv.exists():
        with open(PATHS.users_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for fld in FIELDS:
                    if fld not in row: row[fld] = ""
                users.append(row)
    return users

def write_users(users):
    with open(PATHS.users_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for u in users:
            row = {fld: u.get(fld, "") for fld in FIELDS}
            w.writerow(row)

@router.post("/login")
def login(req: LoginReq):
    for u in read_users():
        if u["username"] == req.username and u["password_hash"] == hash_pw(req.password):
            if u["status"] != "approved":
                raise HTTPException(403, "Pending admin approval")
            tabs = u.get("tabs", "filebrowser,dashboard,splittable")
            if u.get("role") == "admin": tabs = "__all__"
            return {"ok": True, "username": u["username"], "role": u["role"], "tabs": tabs}
    raise HTTPException(401, "Invalid credentials")

@router.post("/register")
def register(req: RegisterReq):
    users = read_users()
    if any(u["username"] == req.username for u in users):
        raise HTTPException(409, "Username exists")
    users.append({"username": req.username, "password_hash": hash_pw(req.password),
                  "role": "user", "status": "pending",
                  "created": datetime.datetime.now().isoformat(),
                  "tabs": "filebrowser,dashboard,splittable"})
    write_users(users)
    send_to_admins("New Registration", f"User '{req.username}' requests approval.", "approval")
    return {"ok": True, "message": "Registered. Wait for admin approval."}

@router.post("/reset-request")
def reset_request(req: ResetReq):
    users = read_users()
    if not any(u["username"] == req.username for u in users):
        raise HTTPException(404, "Username not found")
    send_to_admins("Password Reset Request", f"User '{req.username}' requests password reset.", "approval")
    return {"ok": True, "message": "Reset request sent to admin."}

@router.post("/change-password")
def change_password(req: ChangePwReq):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            if u["password_hash"] != hash_pw(req.old_password):
                raise HTTPException(401, "Current password incorrect")
            u["password_hash"] = hash_pw(req.new_password)
            write_users(users)
            return {"ok": True}
    raise HTTPException(404)
