package local.assistant;

import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;

public final class ApiClient {
    private final String base;
    private final String token; // memory only, never logged or stored in preferences
    public ApiClient(String base, String token) {
        URI uri = URI.create(base);
        boolean local = BuildConfig.DEBUG && "http".equals(uri.getScheme())
            && ("127.0.0.1".equals(uri.getHost()) || "localhost".equals(uri.getHost()) || "10.0.2.2".equals(uri.getHost()));
        if ((!local && !"https".equals(uri.getScheme())) || uri.getHost() == null ||
            uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null)
            throw new IllegalArgumentException("Use HTTPS or a debug loopback endpoint");
        this.base = base.replaceAll("/+$", "");
        this.token = token;
    }
    public JSONObject request(String method, String path, JSONObject body) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) URI.create(base + "/api/v1/" + path).toURL().openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(65000);
            connection.setRequestMethod(method);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setFixedLengthStreamingMode(bytes.length);
                try (java.io.OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            }
            int status = connection.getResponseCode();
            if (status != 200) throw new java.io.IOException("HTTP " + status + "; check status before retrying an action");
            try (InputStream in = connection.getInputStream(); ByteArrayOutputStream buffer = new ByteArrayOutputStream()) {
                byte[] chunk = new byte[4096]; int size;
                while ((size = in.read(chunk)) != -1) {
                    if (buffer.size() + size > 1048576) throw new java.io.IOException("Response too large");
                    buffer.write(chunk, 0, size);
                }
                return new JSONObject(buffer.toString(StandardCharsets.UTF_8.name())).getJSONObject("result");
            }
        } finally { connection.disconnect(); }
    }
}
