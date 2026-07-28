/**
 * Known retailer / vendor domains.
 * URLs from these domains are classified as "where to buy" (vendors) rather
 * than information sources (citations).
 */
const VENDOR_DOMAINS = new Set([
  // UK pharmacy & beauty
  "boots.com",
  "superdrug.com",
  "lookfantastic.com",
  "cultbeauty.co.uk",
  "spacenk.com",
  "beautybay.com",
  "feelunique.com",
  "allbeauty.com",
  "pharmaca.com",
  // UK department stores & general retail
  "johnlewis.com",
  "marksandspencer.com",
  "selfridges.com",
  "harrods.com",
  "libertylondon.com",
  "debenhams.com",
  "next.co.uk",
  "nextdirect.com",
  "tkmaxx.com",
  "tkmaxx.co.uk",
  "hmv.com",
  // UK grocery
  "tesco.com",
  "sainsburys.co.uk",
  "asda.com",
  "waitrose.com",
  "ocado.com",
  "morrisons.com",
  "aldi.co.uk",
  "lidl.co.uk",
  "iceland.co.uk",
  // Fashion / multi-brand
  "asos.com",
  "asos.co.uk",
  "zalando.co.uk",
  "zalando.com",
  "farfetch.com",
  "net-a-porter.com",
  "matchesfashion.com",
  "notonthehighstreet.com",
  "etsy.com",
  // US beauty & pharmacy
  "sephora.com",
  "ulta.com",
  "cvs.com",
  "walgreens.com",
  "target.com",
  "walmart.com",
  "costco.com",
  // US department & general
  "amazon.com",
  "amazon.co.uk",
  "amazon.ca",
  "amazon.com.au",
  "amazon.de",
  "amazon.fr",
  "macys.com",
  "nordstrom.com",
  "bloomingdales.com",
  "kohls.com",
  "jcpenney.com",
  "ebay.com",
  "ebay.co.uk",
  // Hospitality / travel
  "tripadvisor.com",
  "opentable.com",
  "bookatable.co.uk",
  "booking.com",
  "hotels.com",
  "airbnb.com",
  "expedia.com",
  // Food delivery
  "deliveroo.co.uk",
  "ubereats.com",
  "just-eat.co.uk",
  "doordash.com",
  // Health & supplements
  "hollandandbarrett.com",
  "chemistdirect.co.uk",
  "pharmacy2u.co.uk",
  "nhs.uk",
  // DIY / home
  "diy.com",
  "screwfix.com",
  "homebase.co.uk",
  "wickes.co.uk",
  "dunelm.com",
  "argos.co.uk",
  "ikea.com",
]);

/**
 * Returns true if the given domain is a known retailer / vendor.
 * Strips leading "www." before checking.
 */
export function isVendorDomain(domain: string): boolean {
  const d = domain.toLowerCase().replace(/^www\./, "");
  if (VENDOR_DOMAINS.has(d)) return true;
  for (const vendor of VENDOR_DOMAINS) {
    if (d.endsWith(`.${vendor}`)) return true;
  }
  return false;
}

/**
 * Derives a normalised stem from a brand name or domain for fuzzy matching.
 * "Cult Beauty" → "cultbeauty",  "cultbeauty.co.uk" → "cultbeauty"
 * "Space NK"    → "spacenk",     "spacenk.com"       → "spacenk"
 */
function stem(s: string): string {
  return s
    .toLowerCase()
    .replace(/\.(com|co\.uk|co|org|net|io|uk|au|ca|de|fr|es|it)(\.[a-z]{2})?$/, "")
    .replace(/^www\./, "")
    .replace(/[\s\-_'.]+/g, "");
}

// Pre-compute stems of all vendor domains once
const VENDOR_STEMS = new Set(Array.from(VENDOR_DOMAINS).map(stem));

/**
 * Returns true if the competitor name likely refers to a vendor/retailer rather
 * than a competing brand. Handles both brand names ("Boots", "Cult Beauty")
 * and domain strings ("boots.com", "cultbeauty.co.uk").
 */
export function isVendorBrand(name: string): boolean {
  if (isVendorDomain(name)) return true;
  return VENDOR_STEMS.has(stem(name));
}
