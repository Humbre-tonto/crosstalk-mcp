package io.crosstalk;

import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * SQLite-backed message store: one table acting as a per-channel mailbox.
 * Writes are serialized (SQLite single-writer); reads are plain selects.
 */
@Component
public class MessageStore {

    private final String url;
    private final Object writeLock = new Object();

    public MessageStore() {
        String dbPath = System.getenv().getOrDefault("RELAY_DB", "relay.db");
        this.url = "jdbc:sqlite:" + dbPath;
    }

    @PostConstruct
    void init() throws SQLException, ClassNotFoundException {
        Class.forName("org.sqlite.JDBC");
        try (Connection c = conn()) {
            // schema ensured by conn(); just prove connectivity at startup
        }
    }

    /**
     * Open a connection and ensure the schema exists. Done on every connection (cheap, via
     * IF NOT EXISTS) so the relay self-heals if the db file is ever deleted/recreated empty
     * while running, instead of failing with "no such table".
     */
    private Connection conn() throws SQLException {
        Connection c = DriverManager.getConnection(url);
        try (Statement st = c.createStatement()) {
            st.execute("CREATE TABLE IF NOT EXISTS messages("
                    + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    + "channel TEXT NOT NULL,"
                    + "sender TEXT NOT NULL,"
                    + "type TEXT NOT NULL,"
                    + "body TEXT NOT NULL,"
                    + "created_at TEXT NOT NULL)");
            st.execute("CREATE INDEX IF NOT EXISTS idx_channel_id ON messages(channel, id)");
        } catch (SQLException e) {
            c.close();
            throw e;
        }
        return c;
    }

    /** Append a message; returns {id, channel, created_at}. */
    public Map<String, Object> post(String channel, String sender, String type, String body) {
        String ts = Instant.now().toString();
        synchronized (writeLock) {
            try (Connection c = conn();
                 PreparedStatement ps = c.prepareStatement(
                         "INSERT INTO messages(channel,sender,type,body,created_at) VALUES(?,?,?,?,?)",
                         Statement.RETURN_GENERATED_KEYS)) {
                ps.setString(1, channel);
                ps.setString(2, sender);
                ps.setString(3, type);
                ps.setString(4, body);
                ps.setString(5, ts);
                ps.executeUpdate();
                long id;
                try (ResultSet rs = ps.getGeneratedKeys()) {
                    rs.next();
                    id = rs.getLong(1);
                }
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("id", id);
                m.put("channel", channel);
                m.put("created_at", ts);
                return m;
            } catch (SQLException e) {
                throw new RuntimeException("post_message failed: " + e.getMessage(), e);
            }
        }
    }

    /** Return messages in a channel with id > sinceId, oldest first. */
    public List<Map<String, Object>> get(String channel, long sinceId) {
        List<Map<String, Object>> out = new ArrayList<>();
        try (Connection c = conn();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT id,channel,sender,type,body,created_at FROM messages "
                             + "WHERE channel=? AND id>? ORDER BY id")) {
            ps.setString(1, channel);
            ps.setLong(2, sinceId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", rs.getLong("id"));
                    m.put("channel", rs.getString("channel"));
                    m.put("sender", rs.getString("sender"));
                    m.put("type", rs.getString("type"));
                    m.put("body", rs.getString("body"));
                    m.put("created_at", rs.getString("created_at"));
                    out.add(m);
                }
            }
        } catch (SQLException e) {
            throw new RuntimeException("get_messages failed: " + e.getMessage(), e);
        }
        return out;
    }

    /** Channels with message count, last id and last activity timestamp. */
    public List<Map<String, Object>> channels() {
        List<Map<String, Object>> out = new ArrayList<>();
        try (Connection c = conn();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT channel, COUNT(*) cnt, MAX(id) last_id, MAX(created_at) last_at "
                             + "FROM messages GROUP BY channel ORDER BY last_at DESC")) {
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("channel", rs.getString("channel"));
                    m.put("count", rs.getLong("cnt"));
                    m.put("last_id", rs.getLong("last_id"));
                    m.put("last_at", rs.getString("last_at"));
                    out.add(m);
                }
            }
        } catch (SQLException e) {
            throw new RuntimeException("list_channels failed: " + e.getMessage(), e);
        }
        return out;
    }
}
