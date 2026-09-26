package local.assistant;
import org.json.JSONObject;

public final class Models {
    public static final class Confirmation {
        public final String id, tool, status, expires, arguments;
        public Confirmation(JSONObject value) throws Exception {
            id = value.getString("confirmation_id"); tool = value.getString("tool");
            status = value.getString("status"); expires = value.getString("expires_at");
            arguments = value.getJSONObject("arguments").toString(2);
        }
    }
    public static final class ChatReply {
        public final String reply, sessionId;
        public ChatReply(JSONObject value) {
            reply = value.optString("reply", value.optString("status", ""));
            sessionId = value.optString("session_id", "");
        }
    }
}
