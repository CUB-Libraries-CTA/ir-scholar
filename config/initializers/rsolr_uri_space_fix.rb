# frozen_string_literal: true

# Fix double-encoding of spaces in Solr request params (e.g., the `sort` spec).
#
# RSolr::Uri.params_to_solr builds the query string with URI.encode_www_form,
# which encodes spaces as "+" (application/x-www-form-urlencoded). Faraday then
# re-encodes that query string when building the URL, turning each "+" into "%2B".
# Solr decodes "%2B" back to a literal "+", so it receives e.g.,
#   sort=score+desc,+system_create_dtsi+desc
# and rejects every search with:
#   RSolr::Error::Http 400 - "Can't determine a Sort Order (asc or desc) ..."
#
# Forcing spaces to "%20" (instead of "+") avoids the bare "+" that Faraday
# mangles; Solr decodes "%20" to a real space. This affects all sort options
# (relevance, date uploaded, date modified) since each contains a space.
#
# Remove this patch if rsolr/faraday are upgraded to a pairing that no longer
# double-encodes.

require 'rsolr'

module RSolr
  module Uri
    # Re-defines RSolr::Uri.params_to_solr (singleton method; no `super` available).
    # Identical to rsolr-2.6.0 except the escaped branch replaces "+" with "%20".
    def self.params_to_solr(params, escape = true)
      if escape
        return URI.encode_www_form(
          params.reject { |k, v| k.to_s.empty? || v.to_s.empty? }
        ).gsub('+', '%20')
      end

      mapped = params.map do |k, v|
        next if v.to_s.empty?

        if v.instance_of?(::Array)
          params_to_solr(v.map { |x| [k, x] }, false)
        else
          "#{k}=#{v}"
        end
      end
      mapped.compact.join('&')
    end
  end
end
