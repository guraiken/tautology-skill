// passa: fora da arquitetura (worker), mesmo repetido.
fn w(v: &Value) -> String { v.get("w").and_then(Value::as_str).unwrap_or("").to_owned() }
fn z(v: &Value) -> String { v.get("z").and_then(Value::as_str).unwrap_or("").to_owned() }
