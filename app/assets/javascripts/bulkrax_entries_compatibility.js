// Replaces Bulkrax 9.5.1's entries initializer so button toggles are registered
// after the active page-load event instead of being invoked immediately during
// event binding.
function setupButtonToggles() {
  $("button#entry_error").click(function () {
    $("#error_trace").toggle();
  });

  $("button#raw_button").click(function () {
    $("#raw_metadata").toggle();
  });

  $("button#parsed_button").click(function () {
    $("#parsed_metadata").toggle();
  });
}

if (typeof Turbolinks !== 'undefined' && Turbolinks !== null) {
  $(document).on('turbolinks:load', setupButtonToggles);
} else if (typeof Turbo !== 'undefined') {
  $(document).on('turbo:load', setupButtonToggles);
} else {
  $(document).ready(setupButtonToggles);
}
