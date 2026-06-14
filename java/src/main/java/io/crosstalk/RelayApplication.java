package io.crosstalk;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;

/**
 * Crosstalk MCP server.
 *
 * A tiny shared "mailbox" two coding agents (on the same or different machines)
 * connect to, so they can exchange messages and run a back-and-forth until done.
 *
 * Transport: streamable HTTP MCP at /mcp; a REST mirror under /api (Swagger at /swagger-ui.html).
 * Auth:      optional shared bearer token via env RELAY_TOKEN (enforced only if set).
 * Storage:   SQLite file (env RELAY_DB, default relay.db), durable across restarts.
 *
 * Tools / endpoints: post_message, get_messages, list_channels.
 * Run: PORT=8765 java -jar crosstalk-mcp.jar
 */
@SpringBootApplication
public class RelayApplication {

    public static void main(String[] args) {
        String token = System.getenv("RELAY_TOKEN");
        if (token == null || token.isEmpty()) {
            System.out.println("WARNING: RELAY_TOKEN is not set - the relay is OPEN to anyone who can reach it. "
                    + "Set RELAY_TOKEN to require an Authorization: Bearer <token> header.");
        }
        SpringApplication.run(RelayApplication.class, args);
    }

    /** Exposes the relay tools to MCP clients. */
    @Bean
    public ToolCallbackProvider relayToolCallbacks(RelayTools tools) {
        return MethodToolCallbackProvider.builder().toolObjects(tools).build();
    }

    /** Swagger / OpenAPI metadata for the REST mirror. */
    @Bean
    public OpenAPI relayOpenAPI() {
        return new OpenAPI().info(new Info()
                .title("Crosstalk MCP")
                .version("1.0.0")
                .description("A shared mailbox relay for coding agents. Primary interface is the MCP endpoint at "
                        + "POST /mcp (JSON-RPC; tools: post_message, get_messages, list_channels). The REST endpoints "
                        + "below mirror those tools for humans/tools that don't use an MCP client."));
    }
}
