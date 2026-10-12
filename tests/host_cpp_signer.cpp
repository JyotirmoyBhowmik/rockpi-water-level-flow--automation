/**
 * Host-side C++ signer and verifier test runner.
 * Directly includes firmware/common/protocol_v1_1.h to verify bit-level parity
 * between C++ and Python Protocol v1.1 implementations.
 */

#include <iostream>
#include <string>
#include <vector>
#include <cstring>
#include "../firmware/common/protocol_v1_1.h"

int main(int argc, char* argv[]) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <--generate|--verify> <secret_key>\n";
        return 1;
    }

    std::string mode = argv[1];
    std::string key = argv[2];

    if (mode == "--generate") {
        char buf[512];
        const int pumps[4] = {0, 0, 0, 0};

        // 1. Heartbeat
        format_hb_unsigned(buf, sizeof(buf), 1001, 1, 120, true, true, pumps, 1, -65);
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 2. Soil
        format_soil_unsigned(buf, sizeof(buf), 1001, 2, 1, 1.842f);
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 3. Flow
        format_flow_unsigned(buf, sizeof(buf), 1001, 3, 1, 15, 1000);
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 4. Pump
        format_pump_unsigned(buf, sizeof(buf), 1001, 4, 1, true, "cmd", 15);
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 5. Ack
        format_ack_unsigned(buf, sizeof(buf), 1001, 5, "cmd-001", "ok", "");
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 6. Cmd (Pi -> Node)
        format_cmd_unsigned(buf, sizeof(buf), 2002, 1, "cmd-001", 1, 15);
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 7. Stop (Pi -> Node)
        format_stop_unsigned(buf, sizeof(buf), 2002, 2, "cmd-002", 1);
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        // 8. StopAll (Pi -> Node)
        format_stop_all_unsigned(buf, sizeof(buf), 2002, 3, "cmd-003");
        protocol_sign_in_place(buf, sizeof(buf), key.c_str());
        std::cout << buf;

        return 0;
    } else if (mode == "--verify") {
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) continue;
            char buf[1024];
            strncpy(buf, line.c_str(), sizeof(buf) - 1);
            buf[sizeof(buf) - 1] = '\0';

            char extracted_sig[17];
            bool ok = protocol_verify_and_strip_sig(buf, key.c_str(), extracted_sig);
            if (ok) {
                std::cout << "OK " << extracted_sig << "\n";
            } else {
                std::cout << "FAIL\n";
            }
        }
        return 0;
    }

    std::cerr << "Unknown mode: " << mode << "\n";
    return 1;
}
