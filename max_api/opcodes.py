"""MAX messenger WebSocket protocol opcodes."""


class Opcode:
    # Connection
    PING = 1
    ANALYTICS = 5
    INIT = 6
    LOGIN = 19

    # Auth flow
    QR_AUTH_INIT = 288
    QR_AUTH_POLL = 289
    QR_AUTH_COMPLETE = 291
    PASSWORD_AUTH = 115
    TOKEN_REFRESH = 158

    # Contacts
    GET_CONTACTS = 32

    # Chats
    GET_CHATS = 48
    GET_MESSAGES = 49
    MARK_READ = 50
    GET_MEDIA_MESSAGES = 51
    GET_CHATS_UPDATES = 53
    SUBSCRIBE_CHAT = 75
    GET_FOLDERS = 272

    # Messaging
    SEND_MESSAGE = 64
    TYPING = 65
    SEARCH = 60
    SEARCH_CHATS = 68

    # Message extras
    GET_MESSAGE_STATS = 74
    GET_REACTIONS = 180

    # Uploads
    GET_IMAGE_UPLOAD_URL = 80
    GET_FILE_UPLOAD_URL = 87
    CHECK_FILE_UPLOAD = 136

    # Media
    GET_VIDEO = 83

    # Stickers
    GET_STICKER_SETS = 26
    STICKER_SYNC = 27
    ANIMOJI = 28

    # Calls
    INITIATE_CALL = 78
    GET_CALL_HISTORY = 79

    # Social
    GET_USER_STORIES = 177
    GET_COMMON_CHATS = 198

    # Chat state
    SET_CHAT_READ_STATE = 130

    # Server push (cmd=0 from server)
    PUSH_NEW_MESSAGE = 128
    PUSH_CONTENT_ACK = 129
    PUSH_PRESENCE = 132
    PUSH_INCOMING_CALL = 137
    PUSH_BANNERS = 292


class Cmd:
    REQUEST = 0
    RESPONSE = 1
    PUSH = 2
    ERROR = 3
