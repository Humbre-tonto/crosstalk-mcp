package io.crosstalk;

import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

/**
 * The three MCP tools both agents call. Names are kept snake_case so they read
 * naturally as tools (post_message / get_messages / list_channels).
 */
@Service
public class RelayTools {

    private final MessageStore store;

    public RelayTools(MessageStore store) {
        this.store = store;
    }

    @Tool(name = "post_message",
            description = "Append a message to a channel mailbox and return its id. "
                    + "Treat the channel as a shared, possibly internet-reachable bus - do not post secrets/credentials.")
    public Map<String, Object> postMessage(
            @ToolParam(description = "channel name, e.g. \"my-project\"") String channel,
            @ToolParam(description = "who is sending, e.g. \"agent-a\" / \"agent-b\"") String sender,
            @ToolParam(description = "a short label for the message kind, free text (e.g. NOTE, QUESTION, ANSWER, DONE)") String type,
            @ToolParam(description = "the message content (markdown/text)") String body) {
        return store.post(channel, sender, type, body);
    }

    @Tool(name = "get_messages",
            description = "Return messages in a channel with id greater than since_id (use 0 for all). "
                    + "Poll for new messages by passing the highest id you have already seen.")
    public List<Map<String, Object>> getMessages(
            @ToolParam(description = "channel name") String channel,
            @ToolParam(required = false, description = "return messages with id > this value; 0 (default) for all") Long since_id) {
        return store.get(channel, since_id == null ? 0L : since_id);
    }

    @Tool(name = "list_channels",
            description = "List channels with message count, last id, and last activity timestamp.")
    public List<Map<String, Object>> listChannels() {
        return store.channels();
    }
}
