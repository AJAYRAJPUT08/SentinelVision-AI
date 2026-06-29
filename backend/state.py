import threading
import time
from datetime import datetime

class SurveillanceState:
    """
    Thread-safe global state manager for SentinelVision AI surveillance system.
    Manages live metrics, activity debouncing, identity tracking, and threat analytics.
    """
    def __init__(self):
        self._lock = threading.RLock()
        
        # Primary live dashboard state
        self.current_name = ""
        self.confidence = 0
        self.known_faces = 0
        self.unknown_faces = 0
        self.recent_activity = []
        self.threat_level = "LOW"
        self.photo_path = ""
        self.role = ""
        
        # Tracking & Debounce internal memory (CRITICAL REQUIREMENT #8)
        self._last_identity = None
        self._last_identity_time = 0
        self._session_active = False
        self._last_log_time = 0
        self._activity_max_len = 50

    def update_known_faces_count(self, count):
        with self._lock:
            self.known_faces = count

    def update_unknown_faces_count(self, count):
        with self._lock:
            self.unknown_faces = count

    def register_detection(self, name, confidence, is_known, photo_path="", role=""):
        """
        Register a detection event. Enforces state tracking rules:
        - Avoids repeating identity counts frame-by-frame.
        - Updates active identity panel.
        - Triggers a new activity log entry only when a new face appears,
          the identity changes, or at least 5 seconds have passed since
          the last log entry for the same identity - never every frame.
        """
        with self._lock:
            now = time.time()
            self.current_name = name
            self.confidence = int(confidence)
            self.photo_path = photo_path
            self.role = role
            
            # Update threat level based on active identity
            if not is_known:
                self.threat_level = "HIGH"
            else:
                self.threat_level = "LOW"
            
            # State machine logic: Check if this is a new detection session
            time_diff = now - self._last_identity_time
            is_new_identity = (self._last_identity != name)
            is_expired_session = (time_diff > 4.0) # Reset session if unseen for 4 seconds
            is_log_due = (now - self._last_log_time >= 5.0) # heartbeat re-log interval
            
            should_log = (
                is_new_identity
                or is_expired_session
                or not self._session_active
                or is_log_due
            )
            
            if should_log:
                self._last_identity = name
                self._session_active = True
                
                timestamp_str = datetime.now().strftime("%H:%M:%S")
                status_suffix = "VERIFIED" if is_known else "UNAUTHORIZED"
                activity_entry = f"[{timestamp_str}] {name} {status_suffix}"
                
                self.recent_activity.insert(0, activity_entry)
                self._last_log_time = now
                
                # Truncate activity log
                if len(self.recent_activity) > self._activity_max_len:
                    self.recent_activity = self.recent_activity[:self._activity_max_len]
                    
            self._last_identity_time = now


    def clear_current_detection(self):
        """
        Called when no face is detected in the camera frame.
        Clears live panel after a short grace period to prevent flickering.
        """
        with self._lock:
            now = time.time()
            if now - self._last_identity_time > 1.5:
                self.current_name = "NO SUBJECT DETECTED"
                self.confidence = 0
                self.threat_level = "LOW"
                self.photo_path = ""
                self.role = ""
                self._session_active = False

    def get_status_json(self):
        """
        Returns exact requested JSON dictionary structure for /status endpoint.
        """
        with self._lock:
            return {
                "current_name": self.current_name if self.current_name else "NO SUBJECT DETECTED",
                "confidence": self.confidence,
                "known_faces": self.known_faces,
                "unknown_faces": self.unknown_faces,
                "recent_activity": list(self.recent_activity),
                "threat_level": self.threat_level,
                "photo_path": self.photo_path,
                "role": self.role
            }

# Global singleton instance
global_state = SurveillanceState()
