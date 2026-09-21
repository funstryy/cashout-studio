// Just enough JSON for the control protocol.
//
// Both ends of this wire are ours: the studio's Python backend sends flat
// command objects and reads flat replies. That makes a full parser - with
// its nesting, its unicode escapes and its number edge cases - a dependency
// bought for nothing. What is here reads flat objects and arrays of flat
// objects, and refuses anything else rather than guessing.
#pragma once

#include <cstdlib>
#include <string>
#include <unordered_map>
#include <vector>

namespace cashout {
namespace json {

// A parsed value, kept as text plus a type tag. Callers ask for the type
// they expect, which is always known from the protocol.
struct Value {
    enum class Kind { Null, Bool, Number, String } kind{Kind::Null};
    std::string text;
    double number{0.0};
    bool boolean{false};

    std::string asString() const { return text; }
    double asNumber() const { return kind == Kind::Number ? number : std::atof(text.c_str()); }
    long long asInt() const { return static_cast<long long>(asNumber()); }
    bool asBool() const {
        return kind == Kind::Bool ? boolean : (kind == Kind::Number ? number != 0.0 : false);
    }
};

using Object = std::unordered_map<std::string, Value>;

// Parses one flat object. Nested objects and arrays are skipped rather than
// rejected, so adding a field the engine does not care about never breaks an
// older engine.
bool parseObject(const std::string& text, Object& out);

// Escapes a string for output. Control characters go out as \u00XX because a
// raw one in a JSON string is invalid and a Windows path can contain
// anything.
std::string escape(const std::string& raw);

// A small builder, so replies read like the thing they produce.
class Writer {
public:
    Writer& openObject() {
        comma();
        buffer_ += '{';
        first_ = true;
        return *this;
    }
    Writer& closeObject() {
        buffer_ += '}';
        first_ = false;
        return *this;
    }
    Writer& openArray(const char* key) {
        key_(key);
        buffer_ += '[';
        first_ = true;
        return *this;
    }
    Writer& closeArray() {
        buffer_ += ']';
        first_ = false;
        return *this;
    }
    Writer& key(const char* name) {
        key_(name);
        return *this;
    }
    Writer& value(const std::string& text) {
        buffer_ += '"' + escape(text) + '"';
        first_ = false;
        return *this;
    }
    Writer& field(const char* name, const std::string& text) {
        key_(name);
        buffer_ += '"' + escape(text) + '"';
        return *this;
    }
    Writer& field(const char* name, const char* text) { return field(name, std::string(text)); }
    Writer& field(const char* name, double number);
    Writer& field(const char* name, long long number);
    Writer& field(const char* name, int number) { return field(name, static_cast<long long>(number)); }
    Writer& field(const char* name, unsigned long long number) {
        return field(name, static_cast<long long>(number));
    }
    Writer& field(const char* name, bool flag) {
        key_(name);
        buffer_ += flag ? "true" : "false";
        return *this;
    }

    const std::string& str() const { return buffer_; }

private:
    void comma() {
        if (first_ || buffer_.empty()) return;
        // Not after a key's colon either: `openObject()` right after
        // `key("x")` used to emit `"x":,{` - valid-looking enough to miss by
        // eye and a parse error at the other end.
        const char last = buffer_.back();
        if (last == '{' || last == '[' || last == ':') return;
        buffer_ += ',';
    }
    void key_(const char* name) {
        comma();
        buffer_ += '"';
        buffer_ += name;
        buffer_ += "\":";
        first_ = false;
    }

    std::string buffer_;
    bool first_{true};
};

}  // namespace json
}  // namespace cashout
