package io.crosstalk;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * Plain-HTTP mirror of the three MCP relay tools, so anyone holding the jar can see and
 * exercise the API from Swagger UI (/swagger-ui.html) without an MCP client.
 * Both this controller and the MCP tools share the same {@link MessageStore}.
 */
@RestController
@RequestMapping("/api")
@Tag(name = "Relay", description = "Shared mailbox for agent-to-agent messaging. "
        + "Mirrors the MCP tools post_message / get_messages / list_channels.")
public class RelayController {

    private final MessageStore store;

    public RelayController(MessageStore store) {
        this.store = store;
    }

    @Operation(summary = "Post a message to a channel (mirrors post_message)",
            description = "Appends a message to the channel mailbox and returns its id. Do not post secrets.")
    @PostMapping("/channels/{channel}/messages")
    public Map<String, Object> post(
            @Parameter(description = "channel name, e.g. \"my-project\"") @PathVariable String channel,
            @RequestBody PostMessageRequest req) {
        return store.post(channel, req.sender(), req.type(), req.body());
    }

    @Operation(summary = "Get messages from a channel (mirrors get_messages)",
            description = "Returns messages with id greater than since_id (0 = all). "
                    + "Poll for new messages by passing the highest id you have already seen.")
    @GetMapping("/channels/{channel}/messages")
    public List<Map<String, Object>> get(
            @Parameter(description = "channel name") @PathVariable String channel,
            @Parameter(description = "return messages with id > this value; 0 for all")
            @RequestParam(defaultValue = "0") long since_id) {
        return store.get(channel, since_id);
    }

    @Operation(summary = "List channels (mirrors list_channels)",
            description = "Channels with message count, last id, and last activity timestamp.")
    @GetMapping("/channels")
    public List<Map<String, Object>> channels() {
        return store.channels();
    }

    /** Body for posting a message. */
    public record PostMessageRequest(
            @Schema(description = "who is sending", example = "agent-a") String sender,
            @Schema(description = "short label for the message kind (free text)", example = "NOTE") String type,
            @Schema(description = "the message content (markdown/text)", example = "hello from agent-a") String body) {
    }
}
