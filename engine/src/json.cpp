#include "json.h"

#include <cstdio>

namespace cashout {
namespace json {
namespace {

void skipSpace(const std::string& s, std::size_t& i) {
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) ++i;
}

bool readString(const std::string& s, std::size_t& i, std::string& out) {
    if (i >= s.size() || s[i] != '"') return false;
    ++i;
    out.clear();
    while (i < s.size()) {
        const char c = s[i++];
        if (c == '"') return true;
        if (c != '\\') {
            out += c;
            continue;
        }
        if (i >= s.size()) return false;
        const char esc = s[i++];
        switch (esc) {
            case 'n': out += '\n'; break;
            case 't': out += '\t'; break;
            case 'r': out += '\r'; break;
            case 'b': out += '\b'; break;
            case 'f': out += '\f'; break;
            case 'u': {
                // Only the BMP, and only as UTF-8. Paths and device names are
                // the strings that come through here; a surrogate pair in one
                // is possible but vanishingly rare, and mangling it beats
                // rejecting the whole command.
                if (i + 4 > s.size()) return false;
                const unsigned code =
                    static_cast<unsigned>(std::strtoul(s.substr(i, 4).c_str(), nullptr, 16));
                i += 4;
                if (code < 0x80) {
                    out += static_cast<char>(code);
                } else if (code < 0x800) {
                    out += static_cast<char>(0xC0 | (code >> 6));
                    out += static_cast<char>(0x80 | (code & 0x3F));
                } else {
                    out += static_cast<char>(0xE0 | (code >> 12));
                    out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                    out += static_cast<char>(0x80 | (code & 0x3F));
                }
                break;
            }
            default: out += esc; break;
        }
    }
    return false;
}

// Walks past a nested structure without interpreting it, so an unknown field
// costs nothing and never aborts the parse.
void skipNested(const std::string& s, std::size_t& i) {
    int depth = 0;
    bool inString = false;
    while (i < s.size()) {
        const char c = s[i];
        if (inString) {
            if (c == '\\') ++i;
            else if (c == '"') inString = false;
        } else if (c == '"') {
            inString = true;
        } else if (c == '{' || c == '[') {
            ++depth;
        } else if (c == '}' || c == ']') {
            if (--depth == 0) {
                ++i;
                return;
            }
        }
        ++i;
    }
}

}  // namespace

bool parseObject(const std::string& text, Object& out) {
    out.clear();
    std::size_t i = 0;
    skipSpace(text, i);
    if (i >= text.size() || text[i] != '{') return false;
    ++i;

    while (i < text.size()) {
        skipSpace(text, i);
        if (i < text.size() && text[i] == '}') return true;

        std::string name;
        if (!readString(text, i, name)) return false;
        skipSpace(text, i);
        if (i >= text.size() || text[i] != ':') return false;
        ++i;
        skipSpace(text, i);
        if (i >= text.size()) return false;

        Value value;
        const char c = text[i];
        if (c == '"') {
            if (!readString(text, i, value.text)) return false;
            value.kind = Value::Kind::String;
        } else if (c == '{' || c == '[') {
            skipNested(text, i);
            value.kind = Value::Kind::Null;
        } else if (text.compare(i, 4, "true") == 0) {
            i += 4;
            value.kind = Value::Kind::Bool;
            value.boolean = true;
        } else if (text.compare(i, 5, "false") == 0) {
            i += 5;
            value.kind = Value::Kind::Bool;
            value.boolean = false;
        } else if (text.compare(i, 4, "null") == 0) {
            i += 4;
            value.kind = Value::Kind::Null;
        } else {
            const std::size_t start = i;
            while (i < text.size() && (std::isdigit(static_cast<unsigned char>(text[i])) ||
                                       text[i] == '-' || text[i] == '+' || text[i] == '.' ||
                                       text[i] == 'e' || text[i] == 'E')) {
                ++i;
            }
            if (i == start) return false;
            value.text = text.substr(start, i - start);
            value.number = std::atof(value.text.c_str());
            value.kind = Value::Kind::Number;
        }

        out[name] = std::move(value);
        skipSpace(text, i);
        if (i < text.size() && text[i] == ',') ++i;
    }
    return false;
}

std::string escape(const std::string& raw) {
    std::string out;
    out.reserve(raw.size() + 8);
    for (const char c : raw) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

Writer& Writer::field(const char* name, double number) {
    key_(name);
    char buf[40];
    // %.6g rather than the default: a position in seconds printed with
    // seventeen digits is noise, and JSON has no decimal type to be precise
    // about anyway.
    std::snprintf(buf, sizeof(buf), "%.6g", number);
    buffer_ += buf;
    return *this;
}

Writer& Writer::field(const char* name, long long number) {
    key_(name);
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%lld", number);
    buffer_ += buf;
    return *this;
}

}  // namespace json
}  // namespace cashout
