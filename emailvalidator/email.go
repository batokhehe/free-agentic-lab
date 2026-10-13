package emailvalidator

import (
	"strings"
)

// IsValidEmail melakukan validasi format email secara praktis (bukan implementasi penuh RFC).
// Aturan minimal yang diterapkan:
// - String tidak kosong
// - Tidak boleh mengandung spasi/whitespace (termasuk di awal/akhir)
// - Hanya boleh terdapat tepat satu tanda '@'
// - Bagian lokal (local-part) tidak boleh kosong
// - Bagian domain tidak boleh kosong
func IsValidEmail(s string) bool {
	if s == "" {
		return false
	}

	if strings.ContainsAny(s, " \t\r\n\f\v") {
		return false
	}

	if strings.Count(s, "@") != 1 {
		return false
	}

	at := strings.Index(s, "@")
	if at <= 0 {
		return false // local-part kosong (mis. "@example.com")
	}
	if at >= len(s)-1 {
		return false // domain kosong (mis. "user@")
	}

	local, domain := s[:at], s[at+1:]
	if local == "" || domain == "" {
		return false
	}

	return true
}
