import os
import re
import cv2
import time
import numpy as np
from datetime import datetime
from state import global_state


class FastFaceRecognizer:

    def __init__(self,
                 known_dir=None,
                 unknown_dir=None):

        # Resolve paths relative to THIS FILE's location, not the process's
        # current working directory. The previous defaults ("../known_faces")
        # were relative to whatever folder the app happened to be launched
        # from - if started from VISION_AI/ instead of VISION_AI/backend/
        # (a different terminal, an IDE run button, a script, etc.), they
        # silently pointed at the wrong folder, known_faces/ looked empty,
        # training produced 0 faces, and is_trained stayed False forever -
        # which is exactly the "UNKNOWN PERSON (0%)" / "known_faces: 0"
        # symptom seen at runtime.
        backend_dir = os.path.dirname(os.path.abspath(__file__))

        self.known_dir = known_dir or os.path.abspath(
            os.path.join(backend_dir, "..", "known_faces")
        )
        self.unknown_dir = unknown_dir or os.path.abspath(
            os.path.join(backend_dir, "..", "unknown_faces")
        )

        os.makedirs(
            self.known_dir,
            exist_ok=True
        )

        os.makedirs(
            self.unknown_dir,
            exist_ok=True
        )

        cascade_path = (
            cv2.data.haarcascades +
            "haarcascade_frontalface_default.xml"
        )

        self.face_cascade = cv2.CascadeClassifier(
            cascade_path
        )

        self.recognizer = (
            cv2.face.LBPHFaceRecognizer_create()
        )

        self.label_map = {}
        # Maps a person's display name -> canonical photo filename used
        # for the identity panel / lookups (first file registered for them)
        self.person_photo = {}
        self.is_trained = False

        self.last_unknown_save = 0

        # Role / title shown alongside the name in the identity panel and
        # known-faces cards (Fix 3 / Fix 6). Keyed by the same grouped
        # display name used everywhere else. Add more entries here as more
        # people get enrolled - anyone not listed falls back to a sensible
        # default below.
        self.person_role = {
            "AJAY": "ADMIN / OWNER",
            "AJAY RAJPUT": "ADMIN / OWNER",
        }
        self.default_role = "AUTHORIZED USER"

        # Bounded "lock-in" memory so a verified identity doesn't flicker to
        # UNKNOWN for a couple of noisy frames in a row (Fix 4/5). This only
        # ever extends an identity that was ALREADY confidently verified -
        # it never invents a match for someone who was never recognized.
        # Capped at LOCK_SECONDS so a person who actually leaves the frame
        # is correctly cleared, not stuck showing the last name forever.
        self._locked_name = None
        self._lock_started_at = 0
        self.LOCK_SECONDS = 5

        self.load_known_faces()

    def role_for(self, name):
        """Returns the display role/title for a registered person, with a
        safe default for anyone not explicitly listed in person_role."""
        return self.person_role.get(name, self.default_role)

    # --------------------
    # group enrollment files belonging to the same person
    # --------------------

    def person_key_for_file(self, filename):
        """
        Derives a stable person identity key from a filename so that multiple
        enrollment photos of the same person (e.g. "Ajay.jpeg", "Ajay 2.jpeg",
        "Ajay_Rajput_1.jpg") train as ONE identity instead of being split into
        separate labels. Only strips a trailing numeric suffix (space or
        underscore + digits) - distinct names are never merged.
        """
        base = os.path.splitext(filename)[0]
        base = re.sub(r"[ _]\d+$", "", base).strip()
        return base.replace("_", " ").strip().upper()

    # --------------------
    # preprocess
    # --------------------

    def prepare_face(self, face):

        face = cv2.resize(
            face,
            (200, 200)
        )

        face = cv2.equalizeHist(
            face
        )

        return face

    # --------------------
    # load known faces
    # --------------------

    def load_known_faces(self):

        train_faces = []
        train_labels = []

        self.label_map.clear()
        self.person_photo.clear()

        files = [

            f for f in os.listdir(
                self.known_dir
            )

            if f.lower().endswith(
                (".jpg", ".png", ".jpeg")
            )

        ]

        # Group files belonging to the same person (e.g. "Ajay.jpeg",
        # "Ajay 2.jpeg", "Ajay 3.jpeg" all belong to "AJAY") so they train
        # as ONE identity instead of fragmenting into separate weak labels.
        name_to_id = {}
        next_person_id = 1

        for file in files:

            path = os.path.join(
                self.known_dir,
                file
            )

            name = self.person_key_for_file(file)

            img = cv2.imread(path)

            if img is None:
                continue

            gray = cv2.cvtColor(
                img,
                cv2.COLOR_BGR2GRAY
            )

            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.05,
                minNeighbors=3
            )

            if len(faces) > 0:

                x, y, w, h = faces[0]

                crop = gray[
                    y:y+h,
                    x:x+w
                ]

            else:

                crop = gray

            crop = self.prepare_face(
                crop
            )

            if name not in name_to_id:
                name_to_id[name] = next_person_id
                next_person_id += 1
                # First photo seen for this person becomes the canonical
                # photo shown in the identity panel / known faces page.
                self.person_photo[name] = file

            person_id = name_to_id[name]

            # normal image
            train_faces.append(crop)
            train_labels.append(person_id)

            # flipped image
            flip = cv2.flip(
                crop,
                1
            )

            train_faces.append(flip)
            train_labels.append(person_id)

            # bright image
            bright = cv2.convertScaleAbs(
                crop,
                alpha=1.15,
                beta=10
            )

            train_faces.append(bright)
            train_labels.append(person_id)

            self.label_map[
                person_id
            ] = name

        if len(train_faces) > 0:

            self.recognizer.train(
                train_faces,
                np.array(
                    train_labels
                )
            )

            self.is_trained = True

        else:

            self.is_trained = False

        global_state.update_known_faces_count(
            len(self.label_map)
        )

        print(
            "[VISION AI] faces trained:",
            len(self.label_map)
        )

    # --------------------
    # save unknown face
    # --------------------

    def save_unknown(self, frame, x, y, w, h):

        now = time.time()

        if now - self.last_unknown_save < 5:
            return ""

        self.last_unknown_save = now

        filename = "unknown_" + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        ) + ".jpg"

        path = os.path.join(
            self.unknown_dir,
            filename
        )

        crop = frame[
            max(0, y-20):min(frame.shape[0], y+h+20),
            max(0, x-20):min(frame.shape[1], x+w+20)
        ]

        cv2.imwrite(
            path,
            crop
        )

        return "/unknown_photo/" + filename

    # --------------------
    # face matching
    # --------------------

    def recognize_face(self, face):

        if not self.is_trained:
            return "UNKNOWN PERSON", 0, False

        prepared = self.prepare_face(
            face
        )

        label, distance = self.recognizer.predict(
            prepared
        )

        print(
            "LBPH distance =",
            distance
        )

        name = self.label_map.get(
            label,
            "UNKNOWN PERSON"
        )

        now = time.time()

        # STRONG match: confidently the registered person. Refresh the
        # lock so a few seconds of subsequent so-so frames stay stable.
        if distance <= 75:

            confidence = int(
                max(
                    60,
                    100 - (distance * 0.5)
                )
            )

            self._locked_name = name
            self._lock_started_at = now

            return (
                name,
                confidence,
                True
            )

        # BORDERLINE match: not confident enough on raw distance alone.
        # If this exact identity was strongly verified within the last
        # LOCK_SECONDS, keep prioritizing them instead of flipping to
        # UNKNOWN on a single noisy frame (bad lighting/angle jitter) -
        # this is what keeps a demo recording from flickering. The lock
        # is bounded and identity-specific: it never applies to a person
        # who hasn't actually been strongly matched recently, and it
        # expires on its own if nobody is verified for LOCK_SECONDS.
        lock_active = (
            self._locked_name is not None
            and (now - self._lock_started_at) <= self.LOCK_SECONDS
        )

        if distance <= 95 and lock_active and name == self._locked_name:
            confidence = int(max(45, 70 - (distance - 75)))
            return (
                name,
                confidence,
                True
            )

        return (
            "UNKNOWN PERSON",
            20,
            False
        )

    # --------------------
    # process frame
    # --------------------

    def process_frame(self, frame):

        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )

        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(60, 60)
        )

        if len(faces) == 0:

            global_state.clear_current_detection()

            return frame

        for (x, y, w, h) in faces:

            crop = gray[
                y:y+h,
                x:x+w
            ]

            name, conf, is_known = self.recognize_face(
                crop
            )

            photo_path = ""
            role = ""

            if is_known:

                color = (
                    0,
                    255,
                    120
                )

                photo_file = self.person_photo.get(name)

                if photo_file and os.path.exists(
                    os.path.join(self.known_dir, photo_file)
                ):

                    photo_path = "/photo/" + photo_file

                role = self.role_for(name)

            else:

                color = (
                    40,
                    40,
                    255
                )

                photo_path = self.save_unknown(
                    frame,
                    x,
                    y,
                    w,
                    h
                )

            global_state.register_detection(
                name,
                conf,
                is_known,
                photo_path,
                role
            )

            cv2.rectangle(
                frame,
                (x, y),
                (x+w, y+h),
                color,
                2
            )

            label = (
                f"{name} ({conf}%)"
            )

            cv2.putText(
                frame,
                label,
                (x, y-10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                color,
                2
            )

        return frame


# singleton

recognizer_engine = None


def get_recognizer():

    global recognizer_engine

    if recognizer_engine is None:

        recognizer_engine = (
            FastFaceRecognizer()
        )

    return recognizer_engine