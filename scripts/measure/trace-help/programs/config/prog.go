// Разбор настроек: значения по умолчанию, ограничение порта, неизвестные ключи.
package main

import (
	"fmt"
	"strconv"
	"strings"
)

func splitPair(line string) (string, string) {
	i := strings.Index(line, ":")
	if i < 0 {
		return strings.TrimSpace(line), ""
	}
	return strings.TrimSpace(line[:i]), strings.TrimSpace(line[i+1:])
}

func asInt(raw string, fallback int) int {
	n, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return n
}

func asBool(raw string) bool {
	return raw == "да" || raw == "1"
}

func clampPort(p int) int {
	if p < 1024 {
		return 1024
	}
	if p > 65535 {
		return 65535
	}
	return p
}

func complain(key string) int {
	fmt.Println("неизвестный ключ:", key)
	return 0
}

func reset() int {
	return 0
}

func load(lines []string) int {
	port := 8080
	for _, line := range lines {
		key, value := splitPair(line)
		switch key {
		case "порт":
			port = clampPort(asInt(value, port))
		case "отладка":
			if asBool(value) {
				port = port + 1
			}
		case "":
			continue
		default:
			complain(key)
		}
	}
	return port
}

func main() {
	lines := []string{"порт: 80", "отладка: да", "цвет: синий", "", "порт: 9000"}
	fmt.Println("порт", load(lines))
}
