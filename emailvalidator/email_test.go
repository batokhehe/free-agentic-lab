package emailvalidator

import "testing"

func TestIsValidEmail(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want bool
	}{
		// Email valid
		{name: "valid basic", in: "user@example.com", want: true},
		{name: "valid subdomain", in: "alice@mail.example.org", want: true},
		{name: "valid with dot in local", in: "john.doe@example.com", want: true},
		{name: "valid short domain", in: "a@b.co", want: true},

		// Kasus minimal yang diminta
		{name: "empty input", in: "", want: false},
		{name: "no at sign", in: "userexample.com", want: false},
		{name: "local part empty", in: "@example.com", want: false},
		{name: "domain empty", in: "user@", want: false},

		// Format jelas tidak valid
		{name: "double at", in: "a@b@c.com", want: false},
		{name: "space in middle", in: "user name@example.com", want: false},
		{name: "space after", in: "user@example.com ", want: false},
		{name: "space before", in: " user@example.com", want: false},
		{name: "multiple spaces", in: "a b@c.d", want: false},
		{name: "tab before", in: "\tuser@example.com", want: false},
		{name: "tab after", in: "user@example.com\t", want: false},
		{name: "newline after", in: "user@example.com\n", want: false},
		{name: "newline before", in: "\nuser@example.com", want: false},
		{name: "only at", in: "@", want: false},
		{name: "at start and end", in: "@@", want: false},
		{name: "trailing dot in local? practical allowed by simple check", in: "user.@example.com", want: true},
		{name: "leading dot in domain? practical allowed", in: "user@.example.com", want: true},
		{name: "domain without dot (pragmatic)", in: "user@example", want: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsValidEmail(tt.in); got != tt.want {
				t.Errorf("IsValidEmail(%q) = %v, want %v", tt.in, got, tt.want)
			}
		})
	}
}
